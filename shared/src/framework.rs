use anyhow::Result;

pub trait ExecutionWriter {
    fn send_order(&mut self, payload: smallvec::SmallVec<[u8; 128]>);
}

pub trait SovereignEngine {
    type EngineState;
    type RiskState;

    fn on_start(&mut self) -> Result<()>;
    fn on_auth(&mut self);
    fn on_ticker(&mut self, best_bid: u64, best_ask: u64, executor: &mut impl ExecutionWriter);
    fn on_event(&mut self, json_payload: &[u8]);
}
