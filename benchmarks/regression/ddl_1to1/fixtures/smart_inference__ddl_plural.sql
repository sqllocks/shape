
    CREATE TABLE customers (
        customer_id INT IDENTITY(1,1) PRIMARY KEY,
        first_name NVARCHAR(50),
        last_name NVARCHAR(50),
        email NVARCHAR(100),
        status NVARCHAR(20)
    );

    CREATE TABLE orders (
        order_id INT IDENTITY(1,1) PRIMARY KEY,
        customer_id INT NOT NULL,
        order_date DATE NOT NULL,
        total_amount DECIMAL(18,2),
        payment_method NVARCHAR(30),
        status NVARCHAR(20)
    );

    CREATE TABLE order_lines (
        line_id INT IDENTITY(1,1) PRIMARY KEY,
        order_id INT NOT NULL,
        product_id INT NOT NULL,
        quantity INT NOT NULL,
        unit_price DECIMAL(18,2),
        line_total DECIMAL(18,2)
    );

    CREATE TABLE products (
        product_id INT IDENTITY(1,1) PRIMARY KEY,
        name NVARCHAR(100),
        unit_price DECIMAL(18,2),
        unit_cost DECIMAL(18,2),
        weight_kg DECIMAL(8,2),
        category NVARCHAR(30)
    );

    CREATE TABLE categories (
        category_id INT IDENTITY(1,1) PRIMARY KEY,
        name NVARCHAR(50)
    );
    