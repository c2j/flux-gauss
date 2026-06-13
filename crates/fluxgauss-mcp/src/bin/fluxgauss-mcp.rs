#[tokio::main]
async fn main() {
    if let Err(e) = fluxgauss_mcp::run_mcp_server().await {
        eprintln!("FluxGauss MCP server error: {}", e);
        std::process::exit(1);
    }
}
