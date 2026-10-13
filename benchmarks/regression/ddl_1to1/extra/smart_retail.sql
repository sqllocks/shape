-- Exercises every smart-inference rule: roles, semantics, FK distributions, ratios, enums,
-- temporal patterns, correlations and business rules.
CREATE TABLE customers (
    customer_id INT IDENTITY(1,1) PRIMARY KEY,
    first_name NVARCHAR(50) NOT NULL,
    last_name NVARCHAR(50) NOT NULL,
    email NVARCHAR(120),
    date_of_birth DATE,
    gender CHAR(1),
    country VARCHAR(40),
    loyalty_tier VARCHAR(20),
    status VARCHAR(20),
    created_at DATETIME2 DEFAULT GETDATE(),
    updated_at DATETIME2
);

CREATE TABLE products (
    product_id INT IDENTITY(1,1) PRIMARY KEY,
    sku VARCHAR(20),
    product_name VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    price DECIMAL(10,2),
    cost DECIMAL(10,2),
    margin DECIMAL(10,2),
    weight DECIMAL(8,2),
    rating DECIMAL(3,1),
    stock_quantity INT,
    photo VARBINARY(MAX)
);

CREATE TABLE addresses (
    address_id INT IDENTITY(1,1) PRIMARY KEY,
    customer_id INT NOT NULL,
    street VARCHAR(100),
    city VARCHAR(50),
    state VARCHAR(2),
    postal_code VARCHAR(10)
);

CREATE TABLE orders (
    order_id INT IDENTITY(1,1) PRIMARY KEY,
    customer_id INT NOT NULL,
    shipping_address_id INT NULL,
    order_date DATE NOT NULL,
    ship_date DATE,
    subtotal DECIMAL(10,2),
    tax DECIMAL(10,2),
    discount DECIMAL(10,2),
    total DECIMAL(10,2),
    gross_amount DECIMAL(10,2),
    net_amount DECIMAL(10,2),
    status VARCHAR(20),
    payment_method VARCHAR(30),
    priority VARCHAR(10),
    discount_pct DECIMAL(5,2),
    approved_by INT NULL,
    created_at DATETIME2,
    modified_at DATETIME2,
    defect_pct DECIMAL(5,2),
    CONSTRAINT FK_orders_approver FOREIGN KEY (approved_by) REFERENCES employees(employee_id)
);

CREATE TABLE order_items (
    item_id INT IDENTITY(1,1) PRIMARY KEY,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT,
    unit_price DECIMAL(10,2),
    line_total DECIMAL(10,2),
    order_date DATE
);

CREATE TABLE shipment_lines (
    line_id INT IDENTITY(1,1) PRIMARY KEY,
    order_id INT NOT NULL,
    quantity INT,
    amount DECIMAL(10,2)
);

CREATE TABLE order_returns (
    return_id INT IDENTITY(1,1) PRIMARY KEY,
    order_id INT NOT NULL,
    refund_amount DECIMAL(10,2),
    reason VARCHAR(200)
);

CREATE TABLE employees (
    employee_id INT PRIMARY KEY,
    employee_name VARCHAR(80),
    manager_id INT,
    hire_date DATE,
    termination_date DATE,
    salary MONEY,
    CONSTRAINT FK_emp_mgr FOREIGN KEY (manager_id) REFERENCES employees(employee_id)
);

CREATE TABLE audit_log (
    log_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    customer_id INT,
    action VARCHAR(30),
    status VARCHAR(20),
    logged_at DATETIME2
);

CREATE TABLE product_tags (
    product_id INT NOT NULL,
    tag_id INT NOT NULL,
    weight_score INT,
    PRIMARY KEY (product_id, tag_id)
);

CREATE TABLE tags (
    tag_id INT IDENTITY(1,1) PRIMARY KEY,
    tag_name VARCHAR(40)
);

CREATE TABLE dim_store (
    store_key INT PRIMARY KEY,
    store_name VARCHAR(50),
    region VARCHAR(30)
);

CREATE TABLE fact_sales (
    sale_id BIGINT PRIMARY KEY,
    store_key INT NOT NULL,
    sale_date DATE,
    amount DECIMAL(12,2),
    units INT
);

ALTER TABLE product_tags ADD CONSTRAINT FK_pt_product FOREIGN KEY (product_id) REFERENCES products(product_id);
ALTER TABLE product_tags ADD CONSTRAINT FK_pt_tag FOREIGN KEY (tag_id) REFERENCES tags(tag_id);
ALTER TABLE fact_sales ADD CONSTRAINT FK_fs_store FOREIGN KEY (store_key) REFERENCES dim_store(store_key);
ALTER TABLE orders ADD CONSTRAINT FK_orders_addr FOREIGN KEY (shipping_address_id) REFERENCES addresses(address_id);
