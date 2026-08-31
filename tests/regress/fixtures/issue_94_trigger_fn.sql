-- Issue #94: trigger function (RETURNS trigger) NEW/OLD references.
-- Rust engine must stub (should_stub_procedure) instead of emitting bare Java keyword `new`.
CREATE FUNCTION public.last_updated() RETURNS trigger AS $$
BEGIN
    NEW.last_update = current_timestamp;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
