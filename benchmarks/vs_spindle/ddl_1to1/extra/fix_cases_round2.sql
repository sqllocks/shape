-- Inputs written for P4-01c round 2: keys the DDL does not declare (guessed by name), CamelCase
-- keys, and short fixed-length columns (see differences.py, F6 to F8).

-- F6: the parent's key is not called like the child's column; the baseline guesses
-- client.client_id, which does not exist.
CREATE TABLE client (
    cid INT PRIMARY KEY,
    name VARCHAR(60)
);
CREATE TABLE sale (
    id INT PRIMARY KEY,
    client_id INT NOT NULL,
    amount DECIMAL(10,2)
);
-- F6: a parent with a composite key has no single column to point at: no key is guessed.
CREATE TABLE region (
    country INT NOT NULL,
    area INT NOT NULL,
    PRIMARY KEY (country, area)
);
CREATE TABLE shipment (
    id INT PRIMARY KEY,
    region_id INT
);

-- F8: CamelCase keys, the SQL Server convention.
CREATE TABLE Vendor (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    Name NVARCHAR(100)
);
CREATE TABLE PurchaseOrder (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    VendorId INT NOT NULL
);
CREATE TABLE Receipt (
    Id INT IDENTITY(1,1) PRIMARY KEY,
    VendorID INT NOT NULL,
    Memo NVARCHAR(200)
);

-- F7: short fixed-length codes, and value sets longer than the column.
CREATE TABLE locale (
    id INT PRIMARY KEY,
    country_code CHAR(2),
    currency_code CHAR(3),
    currency CHAR(3),
    region_type VARCHAR(4),
    row_status VARCHAR(3),
    status VARCHAR(5),
    product_code VARCHAR(5),
    sku_code VARCHAR(20)
);
