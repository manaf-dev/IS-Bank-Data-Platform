CREATE TABLE [dbo].[dim_merchant] (
    [merchant_key]  BIGINT        NOT NULL,
    [merchant_id]   VARCHAR (MAX) NOT NULL,
    [merchant_name] VARCHAR (MAX) NOT NULL,
    [mcc]           VARCHAR (MAX) NULL,
    [merchant_city] VARCHAR (MAX) NULL
);


GO