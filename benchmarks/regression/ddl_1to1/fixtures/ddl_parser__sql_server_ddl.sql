CREATE TABLE [dbo].[customer] (
    customer_id INT IDENTITY(1,1) NOT NULL,
    first_name NVARCHAR(50) NOT NULL,
    last_name NVARCHAR(50) NOT NULL,
    email NVARCHAR(100),
    is_active BIT DEFAULT 1,
    created_at DATETIME2 DEFAULT GETDATE(),
    CONSTRAINT PK_customer PRIMARY KEY (customer_id)
);

CREATE TABLE [dbo].[order] (
    order_id INT IDENTITY(1,1) NOT NULL,
    customer_id INT NOT NULL,
    order_date DATE NOT NULL,
    total DECIMAL(10,2),
    status VARCHAR(20),
    CONSTRAINT PK_order PRIMARY KEY (order_id),
    CONSTRAINT FK_order_customer FOREIGN KEY (customer_id)
        REFERENCES [dbo].[customer](customer_id)
);
