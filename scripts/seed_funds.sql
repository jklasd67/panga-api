INSERT INTO users (id, full_name, email, api_key, created_at)
VALUES ('user-00000000-0000-4000-8000-000000000001', 'Demo User', 'demo@example.com', 'pk_demo_token', NOW())
ON CONFLICT (id) DO NOTHING;

INSERT INTO accounts (account_number, owner_id, currency, balance, created_at)
VALUES ('DEM00001', 'user-00000000-0000-4000-8000-000000000001', 'EUR', 10000.00, NOW())
ON CONFLICT (account_number) DO NOTHING;
