CREATE TABLE customer (id INT PRIMARY KEY, name VARCHAR(50));
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customer(id));
