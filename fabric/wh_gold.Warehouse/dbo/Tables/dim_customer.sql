CREATE TABLE [dbo].[dim_customer] (
    [customer_key]      BIGINT        NOT NULL,
    [party_id]          VARCHAR (MAX) NULL,
    [customer_type]     VARCHAR (MAX) NULL,
    [display_name]      VARCHAR (MAX) NULL,
    [gender]            VARCHAR (MAX) NULL,
    [age_band]          VARCHAR (MAX) NOT NULL,
    [home_branch_code]  VARCHAR (MAX) NULL,
    [customer_since]    DATE          NULL,
    [cif_count]         BIGINT        NOT NULL,
    [has_duplicate_cif] BIT           NOT NULL,
    [has_kyc_gap]       BIT           NULL
);


GO