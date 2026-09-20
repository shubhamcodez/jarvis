# Jarvis frontend

Dev server proxies `/api` and `/ws` to **`http://localhost:8000`**. If you only run `npm run dev`, Vite will log `ECONNREFUSED` / HTTP 502 until the backend is up.

**Terminal 1 — backend** (from repo root):

```bash
cd backend
poetry run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Desktop window (Tauri): from repo root, `npm run tauri:dev` after Rust is installed. See [DESKTOP.md](../DESKTOP.md).

**Terminal 2 — frontend**:

```bash
cd frontend
npm run dev
```

Then open http://localhost:5173 — `GET http://localhost:8000/health` should return `{"status":"ok"}`.

---

# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.
