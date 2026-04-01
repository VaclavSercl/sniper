// ═══════════════════════════════════════════════════════════
// 🧩 Chat Template Resolver — Model Agnosticism Layer
// v21.0 Pure Rust Hive (#79)
//
// Different GGUF models use different chat templates.
// This module auto-detects the model family from GGUF metadata
// and wraps prompts in the correct template format.
//
// The Logit Sniping prompt itself is model-agnostic (plain text).
// But wrapping it in the right template improves logit quality.
// ═══════════════════════════════════════════════════════════

/// Known model families with distinct chat templates.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ModelFamily {
    /// Microsoft Phi-3.x series
    Phi3,
    /// Meta Llama-3.x series
    Llama3,
    /// Alibaba Qwen2.x series
    Qwen2,
    /// Google Gemma-2 series
    Gemma2,
    /// Mistral / Mixtral series
    Mistral,
    /// Unknown — use raw prompt (no template wrapping)
    Unknown,
}

impl ModelFamily {
    /// Auto-detect model family from GGUF metadata model name.
    /// Called once at boot time from the GGUF Content metadata.
    pub fn detect(model_name: &str) -> Self {
        let lower = model_name.to_lowercase();

        if lower.contains("phi-3") || lower.contains("phi3") {
            ModelFamily::Phi3
        } else if lower.contains("llama-3") || lower.contains("llama3") || lower.contains("meta-llama") {
            ModelFamily::Llama3
        } else if lower.contains("qwen2") || lower.contains("qwen-2") {
            ModelFamily::Qwen2
        } else if lower.contains("gemma-2") || lower.contains("gemma2") {
            ModelFamily::Gemma2
        } else if lower.contains("mistral") || lower.contains("mixtral") {
            ModelFamily::Mistral
        } else {
            ModelFamily::Unknown
        }
    }

    /// Wrap a raw prompt in the model's chat template.
    /// The prompt is used as the "user" message.
    /// System instruction is embedded in the template.
    pub fn wrap_prompt(&self, raw_prompt: &str) -> String {
        match self {
            ModelFamily::Phi3 => format!(
                "<|system|>\n{raw_prompt}<|end|>\n<|assistant|>\n"
            ),
            ModelFamily::Llama3 => format!(
                "<|begin_of_text|><|start_header_id|>user<|end_header_id|>\n\n\
                 {raw_prompt}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
            ),
            ModelFamily::Qwen2 => format!(
                "<|im_start|>user\n{raw_prompt}<|im_end|>\n<|im_start|>assistant\n"
            ),
            ModelFamily::Gemma2 => format!(
                "<start_of_turn>user\n{raw_prompt}<end_of_turn>\n<start_of_turn>model\n"
            ),
            ModelFamily::Mistral => format!(
                "[INST] {raw_prompt} [/INST]"
            ),
            // Unknown: pass raw prompt directly (works for base models)
            ModelFamily::Unknown => raw_prompt.to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_phi3() {
        assert_eq!(ModelFamily::detect("Phi-3.5-mini-instruct"), ModelFamily::Phi3);
        assert_eq!(ModelFamily::detect("phi3-medium"), ModelFamily::Phi3);
    }

    #[test]
    fn test_detect_llama3() {
        assert_eq!(ModelFamily::detect("Meta-Llama-3.2-3B-Instruct"), ModelFamily::Llama3);
    }

    #[test]
    fn test_detect_qwen2() {
        assert_eq!(ModelFamily::detect("Qwen2.5-7B-Instruct"), ModelFamily::Qwen2);
    }

    #[test]
    fn test_detect_unknown() {
        assert_eq!(ModelFamily::detect("some-random-model"), ModelFamily::Unknown);
    }

    #[test]
    fn test_wrap_phi3() {
        let wrapped = ModelFamily::Phi3.wrap_prompt("TEST");
        assert!(wrapped.contains("<|system|>"));
        assert!(wrapped.contains("TEST"));
        assert!(wrapped.contains("<|assistant|>"));
    }

    #[test]
    fn test_wrap_unknown_passthrough() {
        let wrapped = ModelFamily::Unknown.wrap_prompt("RAW PROMPT");
        assert_eq!(wrapped, "RAW PROMPT");
    }
}
