CREATE TABLE [dbo].[dim_account] (
    [account_key]           BIGINT          NOT NULL,
    [account_number]        VARCHAR (MAX)   NULL,
    [account_number_masked] VARCHAR (MAX)   NULL,
    [customer_key]          BIGINT          NOT NULL,
    [product_key]           BIGINT          NOT NULL,
    [branch_key]            BIGINT          NOT NULL,
    [product_code]          VARCHAR (MAX)   NULL,
    [branch_code]           VARCHAR (MAX)   NULL,
    [status]                VARCHAR (MAX)   NULL,
    [opened_date]           DATE            NULL,
    [closed_date]           DATE            NULL,
    [contract_amount]       DECIMAL (18, 2) NULL,
    [interest_rate_pct]     DECIMAL (9, 4)  NULL,
    [term_months]           INT             NULL,
    [maturity_date]         DATE            NULL
);


GO