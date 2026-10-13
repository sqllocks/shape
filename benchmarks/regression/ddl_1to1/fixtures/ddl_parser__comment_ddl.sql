CREATE TABLE dbo.dim_branch (
    branch_key      int          NOT NULL PRIMARY KEY,
    branch_name     varchar(20)  NOT NULL,  /* DALLAS, TAMPA, COLUMBUS, PHOENIX, ATLANTA, DENVER, CHARLOTTE, SAN DIEGO */
    state_code      char(2)      NOT NULL,
    region          varchar(12)  NOT NULL
);

CREATE TABLE dbo.dim_product (--line comment right after the paren
    product_key     int          NOT NULL PRIMARY KEY,
    product_name    varchar(32)  NOT NULL,--e.g. 30YR FIXED, 15YR FIXED
    default_note    varchar(20)  DEFAULT '10' /* not a fraction: 10/2 */
);
