-- Inputs written for P4-01c: each statement exercises one of the behaviours that Shape reads
-- differently from the baseline (see differences.py).
CREATE TABLE customer (
    id INT IDENTITY(1,1) PRIMARY KEY,
    email VARCHAR(100),
    gender CHAR(1),
    marital_status CHAR(1),
    is_vip CHAR(1),
    tier_label NCHAR(1)
);

-- A column-level REFERENCES clause, in the four dialects' spellings.
CREATE TABLE pg_order (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customer (id) ON DELETE CASCADE
);
CREATE TABLE ms_order (
    id INT PRIMARY KEY,
    [CustomerId] INT NOT NULL CONSTRAINT [fk_ms_order_customer] REFERENCES [dbo].[customer] ([id]) ON DELETE SET NULL
);
CREATE TABLE my_order (
    id INT AUTO_INCREMENT PRIMARY KEY,
    `customer_id` INT NULL REFERENCES `customer`(`id`)
);
CREATE TABLE ansi_order (
    id INT PRIMARY KEY,
    customer_id INT REFERENCES customer
);

-- Whole-word names, a parent total over its children, and binary types.
CREATE TABLE invoice (
    id INT PRIMARY KEY,
    invoice_date DATE,
    total DECIMAL(10,2),
    state VARCHAR(50),
    discount_pct DECIMAL(5,2),
    margin_pct DECIMAL(5,2),
    tax_rate DECIMAL(5,4),
    model VARCHAR(40),
    feedback_score DECIMAL(3,1),
    current_value DECIMAL(10,2)
);
CREATE TABLE invoice_line (
    id INT PRIMARY KEY,
    invoice_id INT NOT NULL REFERENCES invoice(id),
    line_total DECIMAL(10,2),
    photo VARBINARY(MAX),
    thumbnail BINARY(MAX),
    raw_document VARBINARY,
    pdf BYTEA,
    picture BLOB,
    big_picture LONGBLOB
);
CREATE TABLE catalog_item (
    id INT PRIMARY KEY,
    invoice_id INT REFERENCES invoice(id),
    title VARCHAR(80)
);
