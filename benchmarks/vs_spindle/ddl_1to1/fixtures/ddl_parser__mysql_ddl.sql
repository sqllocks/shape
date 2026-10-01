CREATE TABLE IF NOT EXISTS `customer` (
    `customer_id` INT AUTO_INCREMENT PRIMARY KEY,
    `first_name` VARCHAR(50) NOT NULL,
    `email` VARCHAR(100)
);

CREATE TABLE `order` (
    `order_id` INT AUTO_INCREMENT PRIMARY KEY,
    `customer_id` INT NOT NULL,
    `order_date` DATE NOT NULL,
    FOREIGN KEY (`customer_id`) REFERENCES `customer`(`customer_id`)
);
