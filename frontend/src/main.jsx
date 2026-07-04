import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { Heart, Home, MapPin, Phone, RefreshCw, X } from "lucide-react";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

function formatPrice(value) {
  if (!value) return "Prix NC";
  return new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 }).format(value);
}

function App() {
  const [listings, setListings] = useState([]);
  const [runs, setRuns] = useState([]);
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(false);

  async function loadListings() {
    const [listingsResponse, runsResponse] = await Promise.all([
      fetch(`${API_URL}/api/listings`),
      fetch(`${API_URL}/api/crawl-runs`),
    ]);
    setListings(await listingsResponse.json());
    setRuns(await runsResponse.json());
  }

  async function runGreenAcresCrawl() {
    setLoading(true);
    await fetch(`${API_URL}/api/crawl/green-acres`, { method: "POST" });
    await loadListings();
    setLoading(false);
  }

  async function setListingStatus(id, nextStatus) {
    await fetch(`${API_URL}/api/listings/${id}/status`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: nextStatus }),
    });
    await loadListings();
  }

  useEffect(() => {
    loadListings();
  }, []);

  const filtered = useMemo(() => {
    if (status === "all") return listings;
    return listings.filter((listing) => listing.status === status);
  }, [listings, status]);

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>Maison Scout</h1>
          <p>Frejus, Saint-Raphael et les maisons qui meritent vraiment un appel.</p>
        </div>
        <button className="primary" onClick={runGreenAcresCrawl} disabled={loading}>
          <RefreshCw size={18} />
          {loading ? "Scan..." : "Scanner Green-Acres"}
        </button>
      </header>

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
