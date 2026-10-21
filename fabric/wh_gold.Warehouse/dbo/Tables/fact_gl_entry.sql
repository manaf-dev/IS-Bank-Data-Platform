CREATE TABLE [dbo].[fact_gl_entry] (
    [month_key]      INT             NULL,
    [branch_key]     BIGINT          NOT NULL,
    [cost_centre]    VARCHAR (MAX)   NULL,
    [gl_account]     VARCHAR (MAX)   NULL,
    [gl_description] VARCHAR (MAX)   NULL,
    [line_type]      VARCHAR (MAX)   NOT NULL,
    [amount]         DECIMAL (18, 2) NULL
);


GO