CREATE TABLE [dbo].[fact_loan] (
    [account_key]              BIGINT          NOT NULL,
    [customer_key]             BIGINT          NOT NULL,
    [product_key]              BIGINT          NOT NULL,
    [branch_key]               BIGINT          NOT NULL,
    [disbursed_date_key]       INT             NULL,
    [principal]                DECIMAL (18, 2) NULL,
    [interest_rate_pct]        DECIMAL (9, 4)  NULL,
    [term_months]              INT             NULL,
    [instalments_due]          BIGINT          NOT NULL,
    [instalments_missed]       BIGINT          NOT NULL,
    [amount_repaid]            DECIMAL (18, 2) NULL,
    [last_missed_date]         DATE            NULL,
    [applicant_monthly_income] DECIMAL (18, 2) NULL,
    [employer_type]            VARCHAR (MAX)   NULL,
    [bureau_score]             INT             NULL,
    [loan_status]              VARCHAR (MAX)   NOT NULL
);


GO