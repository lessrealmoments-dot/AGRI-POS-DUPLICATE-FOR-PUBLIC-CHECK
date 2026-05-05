import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '../components/ui/button';
import { Card, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { ScanBarcode, Wifi, WifiOff, CheckCircle, XCircle, Camera, CameraOff, Smartphone } from 'lucide-react';
import { formatTime } from '../lib/dateFormat';

export default function MobileScannerPage() {
  const { sessionId } = useParams();
  const [status, setStatus] = useState('connecting'); // connecting | connected | error | closed
  const [scanning, setScanning] = useState(false);
  const [lastScan, setLastScan] = useState(null);
  const [scanCount, setScanCount] = useState(0);
  const [branchId, setBranchId] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const scannerRef = useRef(null);

  const API_URL = process.env.REACT_APP_BACKEND_URL;

  // Validate session on load and notify server we're connected
  useEffect(() => {
    if (!sessionId) return;
    (async () => {
      try {
        const res = await fetch(`${API_URL}/api/scanner/session/${sessionId}`);
        if (!res.ok) {
          setStatus('error');
          setErrorMsg('Scanner session expired or invalid');
          return;
        }
        const data = await res.json();
        setBranchId(data.branch_id);

        // Tell the server the phone is now connected
        await fetch(`${API_URL}/api/scanner/connect/${sessionId}`, { method: 'POST' });

        setStatus('connected');
      } catch {
        setStatus('error');
        setErrorMsg('Cannot reach server');
      }
    })();
  }, [sessionId, API_URL]);

  // Send barcode via REST (reliable, no WebSocket needed)
  const lastScanRef = useRef({ barcode: '', time: 0 });
  const sendBarcode = useCallback(async (barcode) => {
    const now = Date.now();
    const last = lastScanRef.current;
    // Same barcode → 5s cooldown
    if (barcode === last.barcode && now - last.time < 5000) return;
    // Different barcode → 300ms cooldown
    if (barcode !== last.barcode && now - last.time < 300) return;
    lastScanRef.current = { barcode, time: now };

    // Vibrate for feedback
    if (navigator.vibrate) navigator.vibrate(100);
    setScanCount(c => c + 1);

    try {
      const res = await fetch(`${API_URL}/api/scanner/scan/${sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ barcode }),
      });
      const result = await res.json();
      setLastScan({
        found: result.found,
        barcode: result.barcode,
        product: result.product || null,
        time: formatTime(),
      });
    } catch {
      setLastScan({ found: false, barcode, time: formatTime() });
    }
  }, [sessionId, API_URL]);

  // Start camera barcode scanner
  const startScanning = useCallback(async () => {
    try {
      const { Html5Qrcode } = await import('html5-qrcode');
      const scanner = new Html5Qrcode('scanner-region');
      scannerRef.current = scanner;

      await scanner.start(
        { facingMode: 'environment' },
        { fps: 2, qrbox: { width: 250, height: 100 }, aspectRatio: 1.777 },
        (decodedText) => {
          sendBarcode(decodedText);
        },
        () => {}
      );
      setScanning(true);
    } catch (err) {
      console.error('Scanner start error:', err);
      setErrorMsg('Camera access denied or not available');
    }
  }, [sendBarcode]);

  const stopScanning = useCallback(async () => {
    if (scannerRef.current) {
      try {
        await scannerRef.current.stop();
        scannerRef.current.clear();
      } catch {}
      scannerRef.current = null;
    }
    setScanning(false);
  }, []);

  useEffect(() => {
    return () => { stopScanning(); };
  }, [stopScanning]);

  if (status === 'error' || status === 'closed') {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <Card className="w-full max-w-sm border-red-200">
          <CardContent className="p-6 text-center">
            <WifiOff size={48} className="mx-auto text-red-400 mb-4" />
            <h2 className="text-lg font-bold text-red-700 mb-2">
              {status === 'closed' ? 'Session Closed' : 'Connection Error'}
            </h2>
            <p className="text-sm text-slate-500">{errorMsg || 'The scanner session has ended.'}</p>
            <p className="text-xs text-slate-400 mt-3">Scan a new QR code from the desktop to start again.</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (status === 'connecting') {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center p-4">
        <Card className="w-full max-w-sm">
          <CardContent className="p-6 text-center">
            <div className="w-10 h-10 border-3 border-emerald-600 border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <h2 className="text-lg font-bold mb-1">Connecting...</h2>
            <p className="text-sm text-slate-500">Linking to desktop scanner session</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-900 text-white" data-testid="mobile-scanner-page">
      {/* Header */}
      <div className="bg-[#1A4D2E] px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Smartphone size={18} />
          <span className="font-bold text-sm">AgriBooks Scanner</span>
        </div>
        <Badge className="bg-emerald-500/20 text-emerald-300 border-emerald-500/30 gap-1">
          <Wifi size={12} /> Connected
        </Badge>
      </div>

      {/* Scan count */}
      <div className="px-4 py-3 bg-slate-800 flex items-center justify-between text-sm">
        <span className="text-slate-400">Scans this session: <strong className="text-white">{scanCount}</strong></span>
        <Badge variant="outline" className="text-slate-400 border-slate-600 text-xs">Branch locked</Badge>
      </div>

      {/* Camera area */}
      <div className="relative">
        <div id="scanner-region" className="w-full" style={{ minHeight: scanning ? 300 : 0 }} />
        {!scanning && (
          <div className="flex items-center justify-center py-16 bg-slate-800/50">
            <Button
              onClick={startScanning}
              size="lg"
              className="bg-[#1A4D2E] hover:bg-emerald-800 text-white gap-2 h-14 px-8 text-lg rounded-xl"
              data-testid="start-scan-btn"
            >
              <Camera size={24} /> Start Scanning
            </Button>
          </div>
        )}
      </div>

      {/* Control bar */}
      {scanning && (
        <div className="px-4 py-3 bg-slate-800 flex justify-center">
          <Button
            onClick={stopScanning}
            variant="outline"
            className="border-red-500/50 text-red-400 hover:bg-red-500/10 gap-2"
            data-testid="stop-scan-btn"
          >
            <CameraOff size={16} /> Stop Camera
          </Button>
        </div>
      )}

      {/* Last scan result */}
      <div className="px-4 py-4 space-y-3">
        <p className="text-xs text-slate-500 uppercase font-medium">Last Scan</p>
        {lastScan ? (
          <Card className={`border ${lastScan.found ? 'border-emerald-500/30 bg-emerald-950/30' : 'border-red-500/30 bg-red-950/30'}`}>
            <CardContent className="p-4">
              <div className="flex items-start gap-3">
                {lastScan.found ? (
                  <CheckCircle size={20} className="text-emerald-400 shrink-0 mt-0.5" />
                ) : (
                  <XCircle size={20} className="text-red-400 shrink-0 mt-0.5" />
                )}
                <div className="flex-1 min-w-0">
                  {lastScan.found ? (
                    <>
                      <p className="font-bold text-emerald-300">{lastScan.product?.name}</p>
                      <p className="text-xs text-slate-400 mt-1">
                        SKU: {lastScan.product?.sku} &middot; Stock: {lastScan.product?.available}
                      </p>
                    </>
                  ) : (
                    <p className="font-bold text-red-300">No product found</p>
                  )}
                  <p className="text-xs text-slate-500 mt-1">
                    Barcode: <code className="text-slate-300">{lastScan.barcode}</code> &middot; {lastScan.time}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card className="border-slate-700 bg-slate-800/50">
            <CardContent className="p-6 text-center">
              <ScanBarcode size={32} className="mx-auto text-slate-600 mb-2" />
              <p className="text-sm text-slate-500">Point camera at a barcode to scan</p>
            </CardContent>
          </Card>
        )}

        <p className="text-[10px] text-slate-600 text-center">
          Scanned products appear on the desktop POS automatically
        </p>
      </div>
    </div>
  );
}
