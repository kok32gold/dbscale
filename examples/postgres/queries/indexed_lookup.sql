-- Healthy: primary-key lookup, should stay flat at every scale.
SELECT id, email, full_name FROM users WHERE id = 42;
