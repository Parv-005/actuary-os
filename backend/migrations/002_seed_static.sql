-- 002_seed_static.sql — static seeds: Demo Actuary, knowledge docs, reference values.
-- Idempotent via ON CONFLICT / WHERE NOT EXISTS.

INSERT INTO users (email, name, role)
VALUES ('demo.actuary@vortex.app', 'Demo Actuary', 'actuary')
ON CONFLICT (email) DO NOTHING;

-- Reserve Methodology v3.0 (superseded) + v3.1 (current)
DO $$
DECLARE v30 UUID; v31 UUID;
BEGIN
  SELECT id INTO v30 FROM knowledge_documents WHERE title='Reserve Methodology' AND version='v3.0';
  IF v30 IS NULL THEN
    INSERT INTO knowledge_documents (title, doc_type, version, effective_date, content_text, tags)
    VALUES ('Reserve Methodology','methodology','v3.0','2025-01-15',
      'Reserve methodology v3.0. Expected severity trend for Commercial Construction: +4.0% YoY. Superseded by v3.1.',
      ARRAY['methodology','reserving','construction'])
    RETURNING id INTO v30;
  END IF;
  SELECT id INTO v31 FROM knowledge_documents WHERE title='Reserve Methodology' AND version='v3.1';
  IF v31 IS NULL THEN
    INSERT INTO knowledge_documents (title, doc_type, version, effective_date, content_text, tags)
    VALUES ('Reserve Methodology','methodology','v3.1','2026-04-01',
      'Reserve methodology v3.1 (current). Expected severity trend for Commercial Construction: +5.0% YoY. Loss-ratio review threshold: portfolio LR move >= 3pp triggers investigation. Assumption variance gate: observed vs configured >= 5pp requires actuary review. AI does not recommend assumption changes.',
      ARRAY['methodology','reserving','construction','assumptions'])
    RETURNING id INTO v31;
  END IF;
  UPDATE knowledge_documents SET superseded_by = v31 WHERE id = v30 AND superseded_by IS NULL;

  IF NOT EXISTS (SELECT 1 FROM knowledge_documents WHERE title='Segment Definitions') THEN
    INSERT INTO knowledge_documents (title, doc_type, version, effective_date, content_text, tags)
    VALUES ('Segment Definitions','definition','1.0','2026-01-01',
      'Products: Commercial (segments Construction, SME, Property, Marine Cargo), Motor, Health, Home. Regions: North, South, East, West. Small-sample rule: claims<20 or policies<10 per segment.',
      ARRAY['definitions','segments']);
  END IF;
END $$;

-- Reference values: recon totals + expected LR + assumption + history series (Apr–Aug 2026)
INSERT INTO reference_values (period, metric_key, dimensions, value, source) VALUES
  ('2026-09','recon_premium_total','{}',120000000,'system_of_record'),
  ('2026-09','recon_claims_total','{}',80000000,'system_of_record'),
  ('2026-09','expected_loss_ratio','{}',0.628,'methodology'),
  ('2026-09','expected_severity_trend','{"product":"Commercial","segment":"Construction"}',0.050,'methodology'),
  ('2026-04','historical_loss_ratio','{}',0.615,'system_of_record'),
  ('2026-05','historical_loss_ratio','{}',0.622,'system_of_record'),
  ('2026-06','historical_loss_ratio','{}',0.618,'system_of_record'),
  ('2026-07','historical_loss_ratio','{}',0.625,'system_of_record'),
  ('2026-08','historical_loss_ratio','{}',0.631,'system_of_record')
ON CONFLICT DO NOTHING;
