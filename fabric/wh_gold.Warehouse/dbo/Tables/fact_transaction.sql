CREATE TABLE [dbo].[fact_transaction] (
    [txn_ref]       VARCHAR (MAX)   NULL,
    [date_key]      INT             NULL,
    [account_key]   BIGINT          NOT NULL,
    [customer_key]  BIGINT          NOT NULL,
    [product_key]   BIGINT          NOT NULL,
    [branch_key]    BIGINT          NOT NULL,
    [channel_key]   BIGINT          NOT NULL,
    [txn_code]      VARCHAR (MAX)   NULL,
    [dr_cr]         VARCHAR (MAX)   NULL,
    [booking_ts]    DATETIME2 (6)   NULL,
    [amount]        DECIMAL (18, 2) NULL,
    [signed_amount] DECIMAL (18, 2) NULL
);


GO