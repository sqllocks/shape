CREATE TABLE categories (category_id INT PRIMARY KEY, category_name VARCHAR(40));
CREATE TABLE companies (company_id INT PRIMARY KEY, company_name VARCHAR(40));
CREATE TABLE boxes (box_id INT PRIMARY KEY, label VARCHAR(40));
CREATE TABLE bus (bus_id INT PRIMARY KEY, label VARCHAR(40));
CREATE TABLE statuses (status_id INT PRIMARY KEY, status_code VARCHAR(10));
CREATE TABLE item (item_id INT PRIMARY KEY, category_id INT, company_id INT, box_id INT, bus_id INT,
                   status_id INT, parent_item_id INT, other_id INT, id INT);
