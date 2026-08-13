-- Shared migration 022 — tenant isolation for administration control-plane data.
-- Existing unscoped rows intentionally remain invisible to app_login until an
-- explicit backfill assigns them. Guessing a tenant would create a data leak.
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['admin_role','admin_user','admin_contact','notification_channel','escalation_rule','deboarding_case'] LOOP
    EXECUTE format('ALTER TABLE platform_ref.%I ADD COLUMN IF NOT EXISTS tenant_id uuid', table_name);
    EXECUTE format('ALTER TABLE platform_ref.%I ALTER COLUMN tenant_id SET DEFAULT NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid', table_name);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON platform_ref.%I (tenant_id)', table_name || '_tenant_idx', table_name);
    EXECUTE format('ALTER TABLE platform_ref.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('DROP POLICY IF EXISTS %I ON platform_ref.%I', table_name || '_tenant_isolation', table_name);
    EXECUTE format($policy$
      CREATE POLICY %I ON platform_ref.%I
      USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
      WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    $policy$, table_name || '_tenant_isolation', table_name);
  END LOOP;
END $$;
