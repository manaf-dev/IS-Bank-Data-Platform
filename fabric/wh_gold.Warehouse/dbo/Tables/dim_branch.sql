CREATE TABLE [dbo].[dim_branch] (
    [branch_key]  BIGINT        NOT NULL,
    [branch_code] VARCHAR (MAX) NULL,
    [branch_name] VARCHAR (MAX) NULL,
    [city]        VARCHAR (MAX) NULL,
    [region]      VARCHAR (MAX) NULL,
    [opened_date] DATE          NULL,
    [cost_centre] VARCHAR (MAX) NULL
);


GO