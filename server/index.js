/**
 * index.js — Express application entry point.
 *
 * Starts a single HTTP server that handles:
 *   - REST API routes mounted under /api/
 *   - WebSocket upgrade (for the Python stream bridge) on the same port
 *
 * Usage:
 *   PORT=3000 node index.js
 */

import 'dotenv/config';
import http from 'http';
import express from 'express';

import creatorsRouter from './routes/creators.js';
import assetsRouter   from './routes/assets.js';
import sessionsRouter from './routes/sessions.js';
import eventsRouter   from './routes/events.js';
import { syncAllCreators } from './services/sync.js';
import { attachWebSocketServer } from './services/stream_bridge.js';

const app  = express();
const PORT = process.env.PORT || 3000;

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Basic request logger (skip for health checks)
app.use((req, _res, next) => {
  if (req.path !== '/health') {
    console.log(`[http] ${req.method} ${req.path}`);
  }
  next();
});

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------
app.get('/health', (_req, res) => res.json({ ok: true }));

app.use('/api/creators', creatorsRouter);
// Asset sub-routes reuse the same :slug param via mergeParams in the router
app.use('/api/creators', assetsRouter);
app.use('/api/sessions', sessionsRouter);
app.use('/api/events',   eventsRouter);

// Catch-all for unmatched routes
app.use((_req, res) => res.status(404).json({ error: 'Not found' }));

// Global error handler
app.use((err, _req, res, _next) => {
  console.error('[server] Unhandled error:', err);
  res.status(500).json({ error: 'Internal server error' });
});

// ---------------------------------------------------------------------------
// HTTP + WebSocket server
// ---------------------------------------------------------------------------
const server = http.createServer(app);

// Attach the WebSocket server to the same HTTP server so both run on PORT.
attachWebSocketServer(server);

server.listen(PORT, async () => {
  console.log(`[server] Listening on port ${PORT}`);

  // Sync all creator assets from Supabase Storage to the local filesystem
  // so the Python engine can load them immediately on the next run.
  try {
    await syncAllCreators();
  } catch (err) {
    // Non-fatal — server still starts; Python can trigger per-creator syncs.
    console.error('[server] Startup sync failed:', err.message);
  }
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('[server] SIGTERM received, shutting down...');
  server.close(() => process.exit(0));
});
