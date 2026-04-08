// ═══════════════════════════════════════════════════════════
// 🤖 BotManifest — AI Auto-Discovery for Sniper Armada Bots
// Sniper Armada · v20.0 Hexagonal Architecture
//
// Enables the L2 Python Orchestrator (Gemini) to automatically
// discover bot parameters, strategies, and capabilities without
// hardcoding bot-specific knowledge.
//
// Each bot exposes a `BotManifest` that describes:
// - Its strategy type and supported venues
// - Configurable parameters with ranges and defaults
// - Required mmap paths and IPC channels
// - GID assignments and risk limits
//
// The Orchestrator calls `manifest()` on each bot to get
// a JSON Schema representation of its configuration space.
// ═══════════════════════════════════════════════════════════

use super::exchange::types::ExchangeId;

/// Strategy type classification for AI routing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StrategyType {
    /// Market making with bid/ask grid (Hydra)
    MarketMaking,
    /// Static grid levels at fixed intervals (Grid)
    GridTrading,
    /// Flash crash catching / dip buying (Moonshot)
    FlashCrash,
    /// Triangular arbitrage within one exchange (Trigon)
    TriangularArb,
    /// Cross-exchange arbitrage (Nexus)
    CrossExchangeArb,
}

impl StrategyType {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::MarketMaking => "market_making",
            Self::GridTrading => "grid_trading",
            Self::FlashCrash => "flash_crash",
            Self::TriangularArb => "triangular_arb",
            Self::CrossExchangeArb => "cross_exchange_arb",
        }
    }
}

/// A single configurable parameter exposed to the AI.
#[derive(Debug, Clone)]
pub struct BotParam {
    /// Parameter name (mmap field name, e.g., "grid_spacing")
    pub name: &'static str,
    /// Human description for AI context
    pub description: &'static str,
    /// Parameter type
    pub param_type: ParamType,
    /// Default value (as string for JSON serialization)
    pub default: &'static str,
    /// Unit of measurement (e.g., "bps", "USD", "BTC", "ms")
    pub unit: &'static str,
}

/// Parameter type with range constraints.
#[derive(Debug, Clone)]
pub enum ParamType {
    /// Integer with [min, max] range
    Int { min: i64, max: i64 },
    /// Float with [min, max] range
    Float { min: f64, max: f64 },
    /// Boolean (on/off toggle)
    Bool,
    /// Enum with named options
    Enum { options: &'static [&'static str] },
}

/// Complete bot manifest — everything the AI needs to know.
#[derive(Debug, Clone)]
pub struct BotManifest {
    /// Bot name (e.g., "Hydra", "Grid", "Moonshot")
    pub name: &'static str,
    /// Version string
    pub version: &'static str,
    /// Strategy classification
    pub strategy: StrategyType,
    /// Group ID for order management
    pub gid: u32,
    /// Supported exchanges (which VenueAdapters this bot can use)
    pub supported_venues: &'static [ExchangeId],
    /// Runner type
    pub runner_type: RunnerType,
    /// Configurable parameters
    pub params: &'static [BotParam],
    /// mmap engine state path
    pub engine_path: &'static str,
    /// mmap risk state path
    pub risk_path: &'static str,
    /// Dashboard HTTP port (if any)
    pub dashboard_port: Option<u16>,
}

/// Runner type classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RunnerType {
    /// Single WebSocket (Grid, Moonshot, Trigon, Nexus)
    SingleWs,
    /// Dual WebSocket: MDATA + EXEC (Hydra)
    DualWs,
}

impl BotManifest {
    /// Serialize manifest to JSON string for AI consumption.
    pub fn to_json(&self) -> String {
        let mut json = String::with_capacity(1024);
        json.push_str("{\n");
        json.push_str(&format!("  \"name\": \"{}\",\n", self.name));
        json.push_str(&format!("  \"version\": \"{}\",\n", self.version));
        json.push_str(&format!("  \"strategy\": \"{}\",\n", self.strategy.as_str()));
        json.push_str(&format!("  \"gid\": {},\n", self.gid));
        json.push_str(&format!("  \"runner\": \"{}\",\n", match self.runner_type {
            RunnerType::SingleWs => "single_ws",
            RunnerType::DualWs => "dual_ws",
        }));

        // Supported venues
        json.push_str("  \"supported_venues\": [");
        for (i, v) in self.supported_venues.iter().enumerate() {
            if i > 0 { json.push_str(", "); }
            json.push_str(&format!("\"{}\"", match v {
                ExchangeId::Bitfinex => "bitfinex",
                ExchangeId::Binance => "binance",

            }));
        }
        json.push_str("],\n");

        // IPC paths
        json.push_str(&format!("  \"engine_path\": \"{}\",\n", self.engine_path));
        json.push_str(&format!("  \"risk_path\": \"{}\",\n", self.risk_path));

        if let Some(port) = self.dashboard_port {
            json.push_str(&format!("  \"dashboard_port\": {},\n", port));
        }

        // Parameters
        json.push_str("  \"parameters\": [\n");
        for (i, p) in self.params.iter().enumerate() {
            json.push_str("    {\n");
            json.push_str(&format!("      \"name\": \"{}\",\n", p.name));
            json.push_str(&format!("      \"description\": \"{}\",\n", p.description));
            json.push_str(&format!("      \"default\": \"{}\",\n", p.default));
            json.push_str(&format!("      \"unit\": \"{}\",\n", p.unit));
            match &p.param_type {
                ParamType::Int { min, max } => {
                    json.push_str(&format!("      \"type\": \"int\",\n      \"min\": {},\n      \"max\": {}\n", min, max));
                }
                ParamType::Float { min, max } => {
                    json.push_str(&format!("      \"type\": \"float\",\n      \"min\": {},\n      \"max\": {}\n", min, max));
                }
                ParamType::Bool => {
                    json.push_str("      \"type\": \"bool\"\n");
                }
                ParamType::Enum { options } => {
                    json.push_str("      \"type\": \"enum\",\n      \"options\": [");
                    for (j, opt) in options.iter().enumerate() {
                        if j > 0 { json.push_str(", "); }
                        json.push_str(&format!("\"{}\"", opt));
                    }
                    json.push_str("]\n");
                }
            }
            json.push_str("    }");
            if i + 1 < self.params.len() { json.push(','); }
            json.push('\n');
        }
        json.push_str("  ]\n");
        json.push('}');
        json
    }
}

// ═══════════════════════════════════════════════════════════
// Pre-built Manifests for Existing Bots
// ═══════════════════════════════════════════════════════════

pub const HYDRA_MANIFEST: BotManifest = BotManifest {
    name: "Hydra",
    version: "20.0",
    strategy: StrategyType::MarketMaking,
    gid: crate::BOT_GID_HYDRA,
    supported_venues: &[ExchangeId::Bitfinex],
    runner_type: RunnerType::DualWs,
    params: &[
        BotParam { name: "grid_spacing", description: "Grid spacing in fixed-point", param_type: ParamType::Int { min: 1_000_000, max: 500_000_000 }, default: "50000000", unit: "price_scale" },
        BotParam { name: "order_usd", description: "Order size in USD", param_type: ParamType::Float { min: 5.0, max: 10000.0 }, default: "20.0", unit: "USD" },
        BotParam { name: "buy_levels", description: "Number of buy grid levels", param_type: ParamType::Int { min: 1, max: 10 }, default: "5", unit: "levels" },
        BotParam { name: "sell_levels", description: "Number of sell grid levels", param_type: ParamType::Int { min: 1, max: 10 }, default: "5", unit: "levels" },
        BotParam { name: "skew_bps", description: "Inventory skew in basis points", param_type: ParamType::Int { min: -500, max: 500 }, default: "0", unit: "bps" },
    ],
    engine_path: "/dev/shm/beroun/engine_state.bin",
    risk_path: "/dev/shm/beroun/risk_state.bin",
    dashboard_port: Some(3000),
};

pub const GRID_MANIFEST: BotManifest = BotManifest {
    name: "Grid",
    version: "20.0",
    strategy: StrategyType::GridTrading,
    gid: crate::BOT_GID_GRID,
    supported_venues: &[ExchangeId::Bitfinex],
    runner_type: RunnerType::SingleWs,
    params: &[
        BotParam { name: "grid_levels", description: "Number of grid levels per side", param_type: ParamType::Int { min: 2, max: 20 }, default: "5", unit: "levels" },
        BotParam { name: "grid_spacing_pct", description: "Spacing between levels", param_type: ParamType::Float { min: 0.05, max: 5.0 }, default: "0.5", unit: "%" },
        BotParam { name: "quantity", description: "BTC amount per level", param_type: ParamType::Float { min: 0.0001, max: 1.0 }, default: "0.001", unit: "BTC" },
    ],
    engine_path: "/dev/shm/beroun/grid_engine.bin",
    risk_path: "/dev/shm/beroun/grid_risk.bin",
    dashboard_port: None,
};

pub const MOONSHOT_MANIFEST: BotManifest = BotManifest {
    name: "Moonshot",
    version: "20.0",
    strategy: StrategyType::FlashCrash,
    gid: crate::BOT_GID_MOONSHOT,
    supported_venues: &[ExchangeId::Bitfinex],
    runner_type: RunnerType::SingleWs,
    params: &[
        BotParam { name: "drop_pct", description: "Flash crash trigger depth", param_type: ParamType::Float { min: 0.5, max: 20.0 }, default: "3.0", unit: "%" },
        BotParam { name: "tp_pct", description: "Take profit percentage", param_type: ParamType::Float { min: 0.1, max: 10.0 }, default: "1.5", unit: "%" },
        BotParam { name: "order_usd", description: "Order size in USD", param_type: ParamType::Float { min: 5.0, max: 5000.0 }, default: "50.0", unit: "USD" },
    ],
    engine_path: "/dev/shm/beroun/moonshot_engine.bin",
    risk_path: "/dev/shm/beroun/moonshot_risk.bin",
    dashboard_port: None,
};

pub const TRIGON_MANIFEST: BotManifest = BotManifest {
    name: "Trigon",
    version: "20.0",
    strategy: StrategyType::TriangularArb,
    gid: crate::BOT_GID_TRIGON,
    supported_venues: &[ExchangeId::Bitfinex],
    runner_type: RunnerType::SingleWs,
    params: &[
        BotParam { name: "min_profit_bps", description: "Minimum profit to execute triangle", param_type: ParamType::Int { min: 1, max: 100 }, default: "5", unit: "bps" },
        BotParam { name: "max_usd", description: "Maximum USD per triangle execution", param_type: ParamType::Float { min: 10.0, max: 50000.0 }, default: "500.0", unit: "USD" },
        BotParam { name: "cooldown_ms", description: "Cooldown between triangle executions", param_type: ParamType::Int { min: 100, max: 60000 }, default: "5000", unit: "ms" },
    ],
    engine_path: "/dev/shm/beroun/trigon_engine.bin",
    risk_path: "/dev/shm/beroun/trigon_risk.bin",
    dashboard_port: None,
};

pub const NEXUS_MANIFEST: BotManifest = BotManifest {
    name: "Nexus",
    version: "20.0",
    strategy: StrategyType::CrossExchangeArb,
    gid: crate::BOT_GID_NEXUS,
    supported_venues: &[ExchangeId::Bitfinex, ExchangeId::Binance],
    runner_type: RunnerType::SingleWs,
    params: &[
        BotParam { name: "min_spread_bps", description: "Minimum spread for arb signal", param_type: ParamType::Int { min: 1, max: 100 }, default: "8", unit: "bps" },
        BotParam { name: "order_usd", description: "Order size in USD", param_type: ParamType::Float { min: 10.0, max: 10000.0 }, default: "100.0", unit: "USD" },
    ],
    engine_path: "/dev/shm/beroun/cross_exchange.bin",
    risk_path: "/dev/shm/beroun/cross_exchange.bin",
    dashboard_port: None,
};

/// Get all registered bot manifests.
pub fn all_manifests() -> &'static [BotManifest] {
    &[HYDRA_MANIFEST, GRID_MANIFEST, MOONSHOT_MANIFEST, TRIGON_MANIFEST, NEXUS_MANIFEST]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_manifest_json() {
        let json = HYDRA_MANIFEST.to_json();
        assert!(json.contains("\"name\": \"Hydra\""));
        assert!(json.contains("\"strategy\": \"market_making\""));
        assert!(json.contains("\"type\": \"int\""));
        assert!(json.contains("grid_spacing"));
    }

    #[test]
    fn test_all_manifests() {
        let manifests = all_manifests();
        assert_eq!(manifests.len(), 5);
        assert_eq!(manifests[0].name, "Hydra");
        assert_eq!(manifests[4].name, "Nexus");
    }
}
