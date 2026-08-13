-- Shared migration 020 — least-privilege permissions for the Go Platform and
-- tenant APIs. app_login owns no objects and receives no CREATE privileges.

GRANT USAGE ON SCHEMA platform_ref, crypto TO app_login;
GRANT SELECT ON TABLE
  platform_ref.api_principal, platform_ref.principal_tenant_role,
  platform_ref.tenant, platform_ref.role, platform_ref.role_permission,
  platform_ref.permission, platform_ref.principal_customer_access,
  platform_ref.document_type, platform_ref.hsn_master, platform_ref.sac_master,
  platform_ref.customer_document, platform_ref.data_requirement
TO app_login;
GRANT SELECT, INSERT, UPDATE ON TABLE
  platform_ref.onboarding_customer, platform_ref.customer_profile,
  platform_ref.onboarding_business_profile, platform_ref.onboarding_product_service,
  platform_ref.gst_registration, platform_ref.customer_document, platform_ref.onboarding_state,
  platform_ref.smart_question_run, platform_ref.smart_question,
  platform_ref.smart_question_response,
  platform_ref.admin_user, platform_ref.admin_role,
  platform_ref.escalation_rule, platform_ref.notification_channel,
  platform_ref.deboarding_case, platform_ref.admin_contact
TO app_login;
GRANT DELETE ON TABLE platform_ref.admin_role, platform_ref.admin_contact TO app_login;
GRANT EXECUTE ON FUNCTION crypto.gen_random_uuid() TO app_login;
GRANT EXECUTE ON FUNCTION crypto.pgp_sym_encrypt(text, text, text) TO app_login;

DO $$
DECLARE
  schema_name text;
  object_name text;
  writable_gold constant text[] := ARRAY[
    'connector_configuration', 'datahub_upload_request', 'workspace_evidence', 'workspace_comment',
    'workspace_decision', 'notice_response_draft'
  ];
  readable_gold constant text[] := ARRAY[
    'connector_configuration', 'datahub_upload_request', 'workspace_assessment', 'workspace_rule_result',
    'workspace_evidence', 'workspace_comment', 'workspace_decision',
    'workspace_activity', 'external_verification', 'reconciliation_result',
    'gst_document', 'gst_document_fact', 'gst_document_validation', 'gst_notice',
    'gst_notice_paragraph', 'notice_response_draft',
    'v1_command_center_data_quality', 'v1_command_center_notices',
    'v1_command_center_actions'
  ];
  readable_silver constant text[] := ARRAY['v1_ingest_batch', 'v1_purchase_invoice', 'purchase_register'];
BEGIN
  FOR schema_name IN
    SELECT nspname FROM pg_namespace WHERE nspname ~ '^t_[a-z0-9_]+_(gold|silver)$'
  LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO app_login', schema_name);
    IF schema_name LIKE '%_gold' THEN
      FOREACH object_name IN ARRAY readable_gold LOOP
        IF to_regclass(format('%I.%I', schema_name, object_name)) IS NOT NULL THEN
          EXECUTE format('GRANT SELECT ON TABLE %I.%I TO app_login', schema_name, object_name);
        END IF;
      END LOOP;
      FOREACH object_name IN ARRAY writable_gold LOOP
        IF to_regclass(format('%I.%I', schema_name, object_name)) IS NOT NULL THEN
          EXECUTE format('GRANT INSERT, UPDATE ON TABLE %I.%I TO app_login', schema_name, object_name);
        END IF;
      END LOOP;
    ELSE
      FOREACH object_name IN ARRAY readable_silver LOOP
        IF to_regclass(format('%I.%I', schema_name, object_name)) IS NOT NULL THEN
          EXECUTE format('GRANT SELECT ON TABLE %I.%I TO app_login', schema_name, object_name);
        END IF;
      END LOOP;
    END IF;
  END LOOP;
END $$;
