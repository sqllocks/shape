CREATE TABLE IF NOT EXISTS "public"."Sales Order" (
    "SalesOrderID" integer GENERATED ALWAYS AS IDENTITY,
    "OrderDate" timestamptz NOT NULL,
    "CustomerID" integer NOT NULL,
    "TotalDue" double precision,
    "Comment" text,
    "Payload" jsonb,
    "RowGuid" uuid,
    "Blob" bytea,
    CONSTRAINT "PK_SalesOrder" PRIMARY KEY ("SalesOrderID" ASC)
);

CREATE TABLE [dbo].[Customer] (
    [CustomerID] [int] IDENTITY(1,1) NOT NULL,
    [AccountNumber] [varchar](10) NOT NULL,
    [ModifiedDate] [datetime] NOT NULL,
    [Rate] [smallmoney] NULL,
    [Score] [real] NULL,
    [Flag] [bit] NULL,
    CONSTRAINT [PK_Customer_CustomerID] PRIMARY KEY CLUSTERED ([CustomerID] ASC)
);

CREATE TABLE `line` (
    `order_id` INT NOT NULL,
    `line_no` SMALLINT NOT NULL,
    `qty` TINYINT,
    `note` VARCHAR(8),
    `long_text` VARCHAR(2000),
    PRIMARY KEY (`order_id`, `line_no` DESC),
    CONSTRAINT fk_line_order FOREIGN KEY (`order_id`) REFERENCES `Sales Order` (`SalesOrderID`)
);

CREATE TABLE "empty_stuff" (id INT);
SELECT 1;
