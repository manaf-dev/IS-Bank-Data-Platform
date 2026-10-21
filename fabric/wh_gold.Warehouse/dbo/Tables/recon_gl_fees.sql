CREATE TABLE [dbo].[recon_gl_fees] (
    [month_key]       INT             NULL,
    [branch_code]     VARCHAR (MAX)   NULL,
    [branch_key]      BIGINT          NOT NULL,
    [gl_fee_income]   DECIMAL (18, 2) NOT NULL,
    [core_fee_income] DECIMAL (18, 2) NOT NULL,
    [difference]      DECIMAL (18, 2) NULL,
    [is_break]        BIT             NULL
);


GO