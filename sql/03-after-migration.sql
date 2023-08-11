SELECT create_hypertable(
    'apc_measurements',
    'at',
    if_not_exists=>true,
    associated_schema_name=>'_powerscout_timescaledb_internal',
    create_default_indexes=>true
);

SELECT add_retention_policy(
    'apc_measurements',
    INTERVAL '6 months',
    if_not_exists=> false
);


SELECT create_hypertable(
    'meters',
    'at',
    if_not_exists=>true,
    associated_schema_name=>'_powerscout_timescaledb_internal',
    create_default_indexes=>true
);
SELECT add_retention_policy(
    'meters',
    INTERVAL '5 years',
    if_not_exists=> false
);