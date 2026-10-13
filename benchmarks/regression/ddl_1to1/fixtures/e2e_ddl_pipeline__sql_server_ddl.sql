CREATE TABLE [dbo].[customer] (
    customer_id INT IDENTITY(1,1) NOT NULL,
    first_name NVARCHAR(50) NOT NULL,
    last_name NVARCHAR(50) NOT NULL,
    email NVARCHAR(100),
    is_active BIT DEFAULT 1,
    created_at DATETIME2 DEFAULT GETDATE(),
    CONSTRAINT PK_customer PRIMARY KEY (customer_id)
);

CREATE TABLE [dbo].[product] (
    product_id INT IDENTITY(1,1) NOT NULL,
    product_name NVARCHAR(200) NOT NULL,
    price DECIMAL(10,2),
    category VARCHAR(50),
    CONSTRAINT PK_product PRIMARY KEY (product_id)
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

CREATE TABLE [dbo].[order_line] (
    line_id INT IDENTITY(1,1) NOT NULL,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT DEFAULT 1,
    line_total DECIMAL(10,2),
    CONSTRAINT PK_order_line PRIMARY KEY (line_id),
    CONSTRAINT FK_line_order FOREIGN KEY (order_id) REFERENCES [dbo].[order](order_id),
    CONSTRAINT FK_line_product FOREIGN KEY (product_id) REFERENCES [dbo].[product](product_id)
);
