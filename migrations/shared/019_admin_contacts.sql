-- Shared migration 019 — contacts used by the administration notification matrix.
CREATE TABLE IF NOT EXISTS platform_ref.admin_contact (
  contact_id uuid PRIMARY KEY DEFAULT crypto.gen_random_uuid(),
  name text NOT NULL, role text NOT NULL, email text NOT NULL UNIQUE, phone text NOT NULL,
  phone_verified boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
