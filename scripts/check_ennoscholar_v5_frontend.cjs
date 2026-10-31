// Offline preload for frontend compilation checks. Not loaded by dev:v5.
// No HTTP, DNS, TCP or fetch may reach an API during this validation.
const net = require('node:net');
const dns = require('node:dns');
const http = require('node:http');
const https = require('node:https');
const fs = require('node:fs');
const path = require('node:path');
const log = path.join(__dirname, '../experiments/ennoscholar_v5/.artifacts/frontend-network-blocked.log');
function refuse(kind) {
  fs.appendFileSync(log, kind + '\n');
  throw new Error('Offline frontend validation: ' + kind + ' is forbidden');
}
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  // Allow only a Windows named pipe for compiler-worker IPC, never TCP.
  const options = args[0];
  const pipe = typeof options === 'string' ? options : options && options.path;
  if (typeof pipe === 'string' && pipe.startsWith('\\\\.\\pipe\\')) return connect.apply(this, args);
  return refuse('TCP connection');
};
dns.lookup = () => refuse('DNS lookup');
dns.resolve = () => refuse('DNS resolve');
if (dns.promises) {
  dns.promises.lookup = async () => refuse('DNS lookup');
  dns.promises.resolve = async () => refuse('DNS resolve');
}
for (const api of [http, https]) {
  api.request = () => refuse('HTTP request');
  api.get = () => refuse('HTTP get');
}
globalThis.fetch = async () => refuse('fetch');
