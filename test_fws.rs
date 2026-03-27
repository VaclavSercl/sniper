# test_fws.rs
use hyper::{Request, body::Bytes, header::{CONNECTION, UPGRADE, HOST}};
use fastwebsockets::{handshake, WebSocket, Frame, Payload};
use tokio::net::TcpStream;
use tokio_native_tls::TlsStream;

struct SpawnExecutor;

impl<Fut> hyper::rt::Executor<Fut> for SpawnExecutor
where
    Fut: std::future::Future + Send + 'static,
    Fut::Output: Send + 'static,
{
    fn execute(&self, fut: Fut) {
        tokio::spawn(fut);
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let tcp = tokio::net::TcpStream::connect("api.bitfinex.com:443").await?;
    tcp.set_nodelay(true)?;
    
    let cx = native_tls::TlsConnector::new()?;
    let cx = tokio_native_tls::TlsConnector::from(cx);
    let tls = cx.connect("api.bitfinex.com", tcp).await?;

    let req = Request::builder()
        .method("GET")
        .uri("wss://api.bitfinex.com/ws/2")
        .header(HOST, "api.bitfinex.com")
        .header(UPGRADE, "websocket")
        .header(CONNECTION, "upgrade")
        .header("Sec-WebSocket-Key", fastwebsockets::handshake::generate_key())
        .header("Sec-WebSocket-Version", "13")
        .body(http_body_util::Empty::<Bytes>::new())?;

    let (mut ws, _) = handshake::client(&SpawnExecutor, req, tls).await?;
    
    let frame = ws.read_frame().await?;
    Ok(())
}

fn main() {}
