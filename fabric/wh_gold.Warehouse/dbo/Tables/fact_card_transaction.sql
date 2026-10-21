CREATE TABLE [dbo].[fact_card_transaction] (
    [event_id]      VARCHAR (MAX)   NULL,
    [date_key]      INT             NULL,
    [event_ts]      DATETIME2 (6)   NULL,
    [hour_of_day]   INT             NULL,
    [is_night]      BIT             NULL,
    [merchant_key]  BIGINT          NOT NULL,
    [account_key]   BIGINT          NOT NULL,
    [customer_key]  BIGINT          NOT NULL,
    [branch_key]    BIGINT          NOT NULL,
    [txn_kind]      VARCHAR (MAX)   NULL,
    [entry_mode]    VARCHAR (MAX)   NULL,
    [merchant_city] VARCHAR (MAX)   NULL,
    [amount]        DECIMAL (18, 2) NULL,
    [response_code] VARCHAR (MAX)   NULL,
    [is_approved]   BIT             NULL,
    [is_late]       BIT             NULL
);


GO