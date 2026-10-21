"""Customer entity resolution: one golden party id across core CIFs, LOS, and bureau records.

The same person can appear as two core CIFs (registered twice, often with a name typo), as
LOS applicants with a differently formatted Ghana Card, and as bureau records. Records are
linked by match rules, and linked records form one party:

- `ghana_card`: the same canonical Ghana Card number (strongest evidence)
- `dob_mobile`: the same date of birth and mobile number
- `dob_name`: the same date of birth and names within a small edit distance

Candidate pairs come from Spark self-joins on blocking keys; clustering is a small,
deterministic union-find on the driver (the pair list is small even at full scale).
"""

import hashlib
from collections.abc import Iterable

from pyspark.sql import functions as F

from isbank.silver.standardize import name_key

NAME_EDIT_DISTANCE = 2
MIN_NAME_LENGTH = 6


def node(system: str, source_id: str) -> str:
    """A record's identity in the match graph."""
    return f"{system}|{source_id}"


def clusters(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Map every node to a party id with union-find; isolated nodes get their own party.

    The party id hashes the cluster's smallest node, so it is stable across runs as long as
    that record exists.
    """
    parent: dict[str, str] = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {n: "PTY-" + hashlib.sha1(find(n).encode()).hexdigest()[:12].upper() for n in parent}


def records(customers_current, applications, bureau):
    """Comparable records from each source: ghana_card, name_key, date_of_birth, mobile."""
    core = customers_current.select(
        F.lit("core").alias("source_system"),
        F.col("cif_number").alias("source_id"),
        "ghana_card",
        F.col("full_name_std").alias("name_key_raw"),
        "date_of_birth",
        "mobile_number",
    )
    core = core.withColumn("name_key", name_key(F.col("name_key_raw"))).drop("name_key_raw")
    los = applications.select(
        F.lit("los").alias("source_system"),
        F.col("application_ref").alias("source_id"),
        "ghana_card",
        "date_of_birth",
        "mobile_number",
        F.col("applicant_name_key").alias("name_key"),
    )
    latest_bureau = bureau.groupBy("bureau_ref").agg(
        F.max_by(F.struct("ghana_card", "name_key", "date_of_birth"), "report_date").alias("r")
    )
    bur = latest_bureau.select(
        F.lit("bureau").alias("source_system"),
        F.col("bureau_ref").alias("source_id"),
        "r.ghana_card",
        "r.date_of_birth",
        F.lit(None).cast("string").alias("mobile_number"),
        "r.name_key",
    )
    columns = [
        "source_system",
        "source_id",
        "ghana_card",
        "name_key",
        "date_of_birth",
        "mobile_number",
    ]
    return core.select(columns).unionByName(los.select(columns)).unionByName(bur.select(columns))


def candidate_pairs(recs):
    """Matching record pairs (a < b) with the rule that matched them."""
    r = recs.withColumn("node", F.concat_ws("|", "source_system", "source_id"))
    a, b = r.alias("a"), r.alias("b")
    ordered = F.col("a.node") < F.col("b.node")
    by_card = a.join(b, (F.col("a.ghana_card") == F.col("b.ghana_card")) & ordered).select(
        F.col("a.node").alias("a"), F.col("b.node").alias("b"), F.lit("ghana_card").alias("rule")
    )
    by_mobile = a.join(
        b,
        (F.col("a.date_of_birth") == F.col("b.date_of_birth"))
        & (F.col("a.mobile_number") == F.col("b.mobile_number"))
        & ordered,
    ).select(
        F.col("a.node").alias("a"), F.col("b.node").alias("b"), F.lit("dob_mobile").alias("rule")
    )
    by_name = a.join(
        b,
        (F.col("a.date_of_birth") == F.col("b.date_of_birth"))
        & ordered
        & (F.length("a.name_key") >= MIN_NAME_LENGTH)
        & (F.levenshtein("a.name_key", "b.name_key") <= NAME_EDIT_DISTANCE),
    ).select(
        F.col("a.node").alias("a"), F.col("b.node").alias("b"), F.lit("dob_name").alias("rule")
    )
    return by_card.unionByName(by_mobile).unionByName(by_name).distinct()


def resolve(spark, recs):
    """party_xref: source_system, source_id, party_id, match_rules (rules that linked it)."""
    pairs = candidate_pairs(recs).collect()
    nodes = [
        row.node
        for row in recs.select(
            F.concat_ws("|", "source_system", "source_id").alias("node")
        ).collect()
    ]
    party = clusters(nodes, [(p.a, p.b) for p in pairs])
    rules: dict[str, set[str]] = {}
    for p in pairs:
        rules.setdefault(p.a, set()).add(p.rule)
        rules.setdefault(p.b, set()).add(p.rule)
    rows = [
        (
            n.split("|", 1)[0],
            n.split("|", 1)[1],
            party[n],
            ",".join(sorted(rules.get(n, set()))) or None,
        )
        for n in nodes
    ]
    import pandas as pd

    frame = pd.DataFrame(rows, columns=["source_system", "source_id", "party_id", "match_rules"])
    # From pandas (Arrow), not a list of tuples, so no Python worker processes are needed.
    return spark.createDataFrame(
        frame, "source_system string, source_id string, party_id string, match_rules string"
    )
