CREATE TABLE [dbo].[fact_account_balance_monthly] (
    [month_key]    INT             NULL,
    [account_key]  BIGINT          NOT NULL,
    [customer_key] BIGINT          NOT NULL,
    [product_key]  BIGINT          NOT NULL,
    [branch_key]   BIGINT          NOT NULL,
    [balance]      DECIMAL (18, 2) NULL
);


GO