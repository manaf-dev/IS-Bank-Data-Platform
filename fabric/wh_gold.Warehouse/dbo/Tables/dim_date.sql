CREATE TABLE [dbo].[dim_date] (
    [date_key]     INT           NULL,
    [date]         DATE          NOT NULL,
    [year]         INT           NOT NULL,
    [quarter]      INT           NOT NULL,
    [month]        INT           NOT NULL,
    [month_name]   VARCHAR (MAX) NOT NULL,
    [month_key]    INT           NULL,
    [day_of_month] INT           NOT NULL,
    [day_name]     VARCHAR (MAX) NOT NULL,
    [is_weekend]   BIT           NOT NULL,
    [is_month_end] BIT           NOT NULL
);


GO