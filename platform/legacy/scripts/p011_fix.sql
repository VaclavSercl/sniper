-- P011 fix (owner-approved): oprava pre-history fills + odstraneni paper kontaminace
-- Spustit jako: sudo -u postgres psql -d beroun -f /tmp/p011_fix.sql

-- 1) Oprava pre-history fills: skutecna data z gateway myTrades (isBuyer=false => SELL)
UPDATE orders SET side='sell', created_at=to_timestamp(1681802099.988) WHERE client_order_id='prehistory-3084776863';
UPDATE orders SET side='sell', created_at=to_timestamp(1682194764.429) WHERE client_order_id='prehistory-3090976286';
UPDATE orders SET side='sell', created_at=to_timestamp(1682501660.606) WHERE client_order_id='prehistory-3094706848';
UPDATE orders SET side='sell', created_at=to_timestamp(1683017315.605) WHERE client_order_id='prehistory-3102872274';
UPDATE fills SET fee=0.00019969, fee_currency='BNB', filled_at=to_timestamp(1681802099.988) WHERE venue_fill_id='3084776863';
UPDATE fills SET fee=0.14979717, fee_currency='USDT', filled_at=to_timestamp(1682194764.429) WHERE venue_fill_id='3090976286';
UPDATE fills SET fee=0.18754008, fee_currency='USDT', filled_at=to_timestamp(1682501660.606) WHERE venue_fill_id='3094706848';
UPDATE fills SET fee=0.10013846, fee_currency='USDT', filled_at=to_timestamp(1683017315.605) WHERE venue_fill_id='3102872274';

-- 2) Paper kontaminace: smazat fills+orders s venue='paper' (backtest artefakty;
--    data zachovana v research_trial_results a research/results/*.json)
DELETE FROM fills WHERE order_id IN (SELECT id FROM orders WHERE venue='paper');
DELETE FROM orders WHERE venue='paper';

-- 3) Verifikace
SELECT 'pre-history fills po oprave:' AS check;
SELECT o.side, f.qty, f.price, f.fee, f.fee_currency, f.filled_at
FROM fills f JOIN orders o ON o.id=f.order_id
WHERE o.client_order_id LIKE 'prehistory-%' ORDER BY f.filled_at;
SELECT 'paper zaznamu po smazani (oczekavano 0):' AS check, count(*) FROM orders WHERE venue='paper';
