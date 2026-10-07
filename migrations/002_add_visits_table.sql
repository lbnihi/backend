-- Migration 002: Add visits table for click tracking
-- Run this on the production database before deploying the new backend version.

CREATE TABLE IF NOT EXISTS visits (
    id              SERIAL PRIMARY KEY,
    ip_address      VARCHAR(45)     NOT NULL,
    country_code    VARCHAR(2),
    city            VARCHAR(100),
    is_vpn          BOOLEAN         NOT NULL DEFAULT FALSE,
    page_url        TEXT,
    referrer        TEXT,
    user_agent      TEXT,
    utm_source      VARCHAR(100),
    utm_medium      VARCHAR(100),
    utm_campaign    VARCHAR(255),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Index for date-range queries in the admin dashboard
CREATE INDEX IF NOT EXISTS ix_visits_created_at ON visits (created_at);

-- Index for filtering valid KSA clicks (non-VPN)
CREATE INDEX IF NOT EXISTS ix_visits_ksa_valid ON visits (created_at, country_code, is_vpn)
    WHERE country_code = 'SA' AND is_vpn = FALSE;
