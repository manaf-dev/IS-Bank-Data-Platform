CREATE TABLE [dbo].[app_user_link] (
    [user_id]      VARCHAR (MAX) NULL,
    [customer_key] BIGINT        NOT NULL,
    [matches]      BIGINT        NOT NULL
);


GO