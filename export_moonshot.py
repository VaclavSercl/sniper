with open('/tmp/issue12.md', 'w') as f:
    f.write("# MOONSHOT (Issue 12) - Analýza Mozku\n\n")
    f.write("## moonshot/src/main.rs\n```rust\n")
    f.write(open('/home/wwwenda/sniper/moonshot/src/main.rs').read())
    f.write("\n```\n\n## moonshot/src/book.rs (The Slippage Matrix)\n```rust\n")
    f.write(open('/home/wwwenda/sniper/moonshot/src/book.rs').read())
    f.write("\n```\n")
