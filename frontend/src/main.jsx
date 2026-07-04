import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Heart, Home, LogOut, MapPin, Phone, Plus, RefreshCw, X } from "lucide-react";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function formatPrice(value) {
  if (!value) return "Prix NC";
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("maisonScoutToken") || "");
  const [user, setUser] = useState(null);
  const [listings, setListings] = useState([]);
  const [runs, setRuns] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(false);
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ email: "", password: "", display_name: "" });
  const [newCity, setNewCity] = useState("");
  const [error, setError] = useState("");

  function authHeaders() {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  async function loadListings() {
    if (!token) return;
    const [meResponse, listingsResponse, runsResponse, profilesResponse] = await Promise.all([
      fetch(`${API_URL}/api/me`, { headers: authHeaders() }),
      fetch(`${API_URL}/api/listings`, { headers: authHeaders() }),
      fetch(`${API_URL}/api/crawl-runs`, { headers: authHeaders() }),
      fetch(`${API_URL}/api/search-profiles`, { headers: authHeaders() }),
    ]);
    if (meResponse.status === 401) {
      logout();
      return;
    }
    setUser(await meResponse.json());
    setListings(await listingsResponse.json());
    setRuns(await runsResponse.json());
    setProfiles(await profilesResponse.json());
  }

  async function submitAuth(event) {
    event.preventDefault();
    setError("");
    const response = await fetch(`${API_URL}/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(authForm),
    });
    if (!response.ok) {
      setError(authMode === "login" ? "Connexion impossible" : "Inscription impossible");
      return;
    }
    const data = await response.json();
    localStorage.setItem("maisonScoutToken", data.token);
    setToken(data.token);
    setUser(data.user);
  }

  function logout() {
    localStorage.removeItem("maisonScoutToken");
    setToken("");
    setUser(null);
    setListings([]);
    setRuns([]);
    setProfiles([]);
  }

  async function runGreenAcresCrawl() {
    setLoading(true);
    await fetch(`${API_URL}/api/crawl/green-acres`, { method: "POST", headers: authHeaders() });
    await loadListings();
    setLoading(false);
  }

  async function setListingStatus(id, nextStatus) {
    await fetch(`${API_URL}/api/listings/${id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ status: nextStatus }),
    });
    await loadListings();
  }

  async function addCity(event) {
    event.preventDefault();
    if (!newCity.trim()) return;
    await fetch(`${API_URL}/api/search-profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ city: newCity.trim() }),
    });
    setNewCity("");
    await loadListings();
  }

  useEffect(() => {
    loadListings();
  }, [token]);

  const filtered = useMemo(() => {
    if (status === "all") return listings;
    return listings.filter((listing) => listing.status === status);
  }, [listings, status]);

  if (!token) {
    return (
      <main className="auth-layout">
        <section className="auth-panel">
          <h1>Maison Scout</h1>
          <p>Connecte-toi pour suivre tes villes, trier les annonces et lancer les scans.</p>
          <form onSubmit={submitAuth}>
            {authMode === "register" && (
              <input
                placeholder="Nom affiché"
                value={authForm.display_name}
                onChange={(event) => setAuthForm({ ...authForm, display_name: event.target.value })}
              />
            )}
            <input
              placeholder="Email"
              type="email"
              value={authForm.email}
              onChange={(event) => setAuthForm({ ...authForm, email: event.target.value })}
            />
            <input
              placeholder="Mot de passe"
              type="password"
              value={authForm.password}
              onChange={(event) => setAuthForm({ ...authForm, password: event.target.value })}
            />
            {error && <p className="error">{error}</p>}
            <button className="primary" type="submit">
              {authMode === "login" ? "Se connecter" : "Créer le compte"}
            </button>
          </form>
          <button className="link-button" onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}>
            {authMode === "login" ? "Créer un compte" : "J'ai déjà un compte"}
          </button>
        </section>
      </main>
    );
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>Maison Scout</h1>
          <p>{user?.display_name || "Compte"} · annonces centralisees par ville suivie.</p>
        </div>
        <div className="topbar-actions">
          <button className="primary" onClick={runGreenAcresCrawl} disabled={loading}>
            <RefreshCw size={18} />
            {loading ? "Scan..." : "Scanner"}
          </button>
          <button className="icon-button" title="Déconnexion" onClick={logout}>
            <LogOut size={18} />
          </button>
        </div>
      </header>

      <section className="profiles">
        <div className="profile-list">
          {profiles.map((profile) => (
            <span key={profile.id}>{profile.city}</span>
          ))}
        </div>
        <form onSubmit={addCity}>
          <input placeholder="Ajouter une ville" value={newCity} onChange={(event) => setNewCity(event.target.value)} />
          <button title="Ajouter" type="submit">
            <Plus size={18} />
          </button>
        </form>
      </section>

      <section className="filters">
        {[
          ["all", "Toutes"],
          ["new", "Nouvelles"],
          ["favorite", "Shortlist"],
          ["call", "A appeler"],
          ["rejected", "Rejetees"],
        ].map(([value, label]) => (
          <button key={value} className={status === value ? "active" : ""} onClick={() => setStatus(value)}>
            {label}
          </button>
        ))}
      </section>

      <section className="summary">
        <div>
          <strong>{listings.length}</strong>
          <span>annonces suivies</span>
        </div>
        <div>
          <strong>{runs[0]?.found_count ?? 0}</strong>
          <span>trouvees au dernier scan</span>
        </div>
        <div>
          <strong>{runs[0]?.status ?? "jamais"}</strong>
          <span>statut crawler</span>
        </div>
      </section>

      <section className="grid">
        {filtered.map((listing) => (
          <article className="card" key={listing.id}>
            <div className="photo">
              {listing.photos[0] ? <img src={listing.photos[0].url} alt={listing.title} /> : <Home size={44} />}
              <span>{listing.score ?? "-"} / 100</span>
            </div>
            <div className="content">
              <h2>{listing.title}</h2>
              <p className="location">
                <MapPin size={16} />
                {listing.city} {listing.postal_code || ""}
              </p>
              <p className="price">{formatPrice(listing.price_eur)}</p>
              <p className="meta">
                {listing.living_area_m2 || "?"} m2 hab. · {listing.land_area_m2 || "?"} m2 terrain ·{" "}
                {listing.bedrooms || "?"} ch.
              </p>
              <p className="description">{listing.description}</p>
              <a className="source" href={listing.sources[0]?.url} target="_blank" rel="noreferrer">
                Voir l'annonce source
              </a>
              <div className="actions">
                <button title="Shortlist" onClick={() => setListingStatus(listing.id, "favorite")}>
                  <Heart size={18} />
                </button>
                <button title="A appeler" onClick={() => setListingStatus(listing.id, "call")}>
                  <Phone size={18} />
                </button>
                <button title="Rejeter" onClick={() => setListingStatus(listing.id, "rejected")}>
                  <X size={18} />
                </button>
              </div>
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
