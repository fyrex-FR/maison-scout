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
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(false);

  async function loadListings() {
    const response = await fetch(`${API_URL}/api/listings`);
    setListings(await response.json());
  }

  async function runDemoCrawl() {
    setLoading(true);
    await fetch(`${API_URL}/api/crawl/demo`, { method: "POST" });
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
        <button className="primary" onClick={runDemoCrawl} disabled={loading}>
          <RefreshCw size={18} />
          {loading ? "Scan..." : "Scanner"}
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

      <section className="grid">
        {filtered.map((listing) => (
          <article className="card" key={listing.id}>
            <div className="photo">
              <Home size={44} />
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

