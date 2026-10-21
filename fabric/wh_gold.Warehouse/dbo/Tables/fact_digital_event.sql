CREATE TABLE [dbo].[fact_digital_event] (
    [event_id]          VARCHAR (MAX)   NULL,
    [date_key]          INT             NULL,
    [event_ts]          DATETIME2 (6)   NULL,
    [event_type]        VARCHAR (MAX)   NULL,
    [customer_key]      BIGINT          NOT NULL,
    [user_id]           VARCHAR (MAX)   NULL,
    [device_os]         VARCHAR (MAX)   NULL,
    [app_major_version] INT             NULL,
    [biometric_used]    BIT             NULL,
    [status]            VARCHAR (MAX)   NULL,
    [amount]            DECIMAL (18, 2) NULL,
    [is_late]           BIT             NULL
);


GO