CREATE TABLE [dbo].[fact_loan_payment] (
    [account_key]    BIGINT          NOT NULL,
    [installment_no] INT             NULL,
    [due_date_key]   INT             NULL,
    [amount_due]     DECIMAL (18, 2) NULL,
    [amount_paid]    DECIMAL (18, 2) NULL,
    [is_missed]      BIT             NULL
);


GO