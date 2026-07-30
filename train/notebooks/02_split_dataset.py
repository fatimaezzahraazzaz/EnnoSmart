"""
====================================================================
VISUALISATION DES COURBES DE TRAINING
====================================================================

À lancer PENDANT ou APRÈS le training pour voir les courbes.

Usage:
    python plot_training_curves.py
"""

import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE_DIR = Path(r"C:\EnnoSmart")
MODEL_DIR = BASE_DIR / "models" / "gliner_cir_v2"
CSV_PATH = MODEL_DIR / "training_log.csv"


def load_history(csv_path):
    """Charge l'historique depuis le CSV"""
    history = []
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry = {}
            for k, v in row.items():
                if v == "" or v is None:
                    entry[k] = None
                else:
                    try:
                        entry[k] = float(v)
                    except ValueError:
                        entry[k] = v
            history.append(entry)
    
    return history


def plot_all_curves(history, output_dir):
    """Génère toutes les courbes"""
    
    if not history:
        print("⚠️ Pas de données dans le CSV")
        return
    
    steps = [h["step"] for h in history]
    
    # ===== 1. LOSS =====
    fig, ax = plt.subplots(figsize=(12, 6))
    
    train_losses = [(h["step"], h["train_loss"]) for h in history if h.get("train_loss") is not None]
    val_losses = [(h["step"], h["val_loss"]) for h in history if h.get("val_loss") is not None]
    
    if train_losses:
        ax.plot([t[0] for t in train_losses], [t[1] for t in train_losses],
                marker='o', label='Train Loss', color='#2E86AB', linewidth=2)
    if val_losses:
        ax.plot([v[0] for v in val_losses], [v[1] for v in val_losses],
                marker='s', label='Val Loss', color='#E63946', linewidth=2)
    
    ax.set_xlabel('Step', fontsize=12)
    ax.set_ylabel('Loss', fontsize=12)
    ax.set_title('Training & Validation Loss', fontsize=14, fontweight='bold')
    ax.legend(fontsize=11, loc='best')
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    loss_path = output_dir / "curves_loss.png"
    plt.savefig(loss_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f"✅ {loss_path}")
    
    # ===== 2. F1 + PRECISION + RECALL =====
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # F1 train vs val
    train_f1 = [h.get("train_f1") for h in history]
    val_f1 = [h.get("val_f1") for h in history]
    
    axes[0].plot(steps, train_f1, marker='o', label='Train F1', color='#06A77D', linewidth=2)
    axes[0].plot(steps, val_f1, marker='s', label='Val F1', color='#D62246', linewidth=2)
    axes[0].set_xlabel('Step', fontsize=12)
    axes[0].set_ylabel('F1 Score', fontsize=12)
    axes[0].set_title('Train vs Val F1 Score', fontsize=14, fontweight='bold')
    axes[0].legend(fontsize=11, loc='best')
    axes[0].grid(True, alpha=0.3)
    axes[0].set_ylim(0, 1)
    
    # Highlight le meilleur F1
    if val_f1:
        valid_f1 = [(i, f) for i, f in enumerate(val_f1) if f is not None]
        if valid_f1:
            best_idx, best_f1 = max(valid_f1, key=lambda x: x[1])
            best_step = steps[best_idx]
            axes[0].axvline(x=best_step, color='gold', linestyle='--', alpha=0.7,
                           label=f'Best Val F1 = {best_f1:.3f}')
            axes[0].legend(fontsize=11)
    
    # Precision/Recall val
    val_p = [h.get("val_precision") for h in history]
    val_r = [h.get("val_recall") for h in history]
    
    axes[1].plot(steps, val_p, marker='o', label='Val Precision', color='#F18F01', linewidth=2)
    axes[1].plot(steps, val_r, marker='s', label='Val Recall', color='#A23B72', linewidth=2)
    axes[1].plot(steps, val_f1, marker='^', label='Val F1', color='#2E86AB', linewidth=2.5)
    axes[1].set_xlabel('Step', fontsize=12)
    axes[1].set_ylabel('Score', fontsize=12)
    axes[1].set_title('Validation : Precision / Recall / F1', fontsize=14, fontweight='bold')
    axes[1].legend(fontsize=11, loc='best')
    axes[1].grid(True, alpha=0.3)
    axes[1].set_ylim(0, 1)
    
    plt.tight_layout()
    f1_path = output_dir / "curves_f1.png"
    plt.savefig(f1_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f"✅ {f1_path}")
    
    # ===== 3. COURBE COMBINÉE (Dashboard) =====
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    
    # Top-left : Loss
    if train_losses:
        axes[0][0].plot([t[0] for t in train_losses], [t[1] for t in train_losses],
                       marker='o', label='Train', color='#2E86AB', linewidth=2)
    if val_losses:
        axes[0][0].plot([v[0] for v in val_losses], [v[1] for v in val_losses],
                       marker='s', label='Val', color='#E63946', linewidth=2)
    axes[0][0].set_title('Loss', fontsize=13, fontweight='bold')
    axes[0][0].set_xlabel('Step')
    axes[0][0].set_ylabel('Loss')
    axes[0][0].legend()
    axes[0][0].grid(True, alpha=0.3)
    
    # Top-right : F1
    axes[0][1].plot(steps, train_f1, marker='o', label='Train', color='#06A77D', linewidth=2)
    axes[0][1].plot(steps, val_f1, marker='s', label='Val', color='#D62246', linewidth=2)
    axes[0][1].set_title('F1 Score', fontsize=13, fontweight='bold')
    axes[0][1].set_xlabel('Step')
    axes[0][1].set_ylabel('F1')
    axes[0][1].set_ylim(0, 1)
    axes[0][1].legend()
    axes[0][1].grid(True, alpha=0.3)
    
    # Bottom-left : Precision val
    axes[1][0].plot(steps, val_p, marker='o', color='#F18F01', linewidth=2)
    axes[1][0].set_title('Val Precision', fontsize=13, fontweight='bold')
    axes[1][0].set_xlabel('Step')
    axes[1][0].set_ylabel('Precision')
    axes[1][0].set_ylim(0, 1)
    axes[1][0].grid(True, alpha=0.3)
    
    # Bottom-right : Recall val
    axes[1][1].plot(steps, val_r, marker='s', color='#A23B72', linewidth=2)
    axes[1][1].set_title('Val Recall', fontsize=13, fontweight='bold')
    axes[1][1].set_xlabel('Step')
    axes[1][1].set_ylabel('Recall')
    axes[1][1].set_ylim(0, 1)
    axes[1][1].grid(True, alpha=0.3)
    
    plt.suptitle('Training Dashboard - GLiNER CIR V2', fontsize=16, fontweight='bold', y=1.00)
    plt.tight_layout()
    
    dashboard_path = output_dir / "dashboard.png"
    plt.savefig(dashboard_path, dpi=120, bbox_inches='tight')
    plt.close()
    print(f"✅ {dashboard_path}")
    
    # Stats
    print(f"\n📊 RÉSUMÉ")
    print("-" * 50)
    
    if val_f1:
        valid_f1 = [(i, f) for i, f in enumerate(val_f1) if f is not None]
        if valid_f1:
            best_idx, best_f1 = max(valid_f1, key=lambda x: x[1])
            print(f"  Meilleur Val F1   : {best_f1:.4f} (step {steps[best_idx]})")
            print(f"  Val F1 actuel     : {val_f1[-1]:.4f}")
    
    if val_losses:
        best_loss = min(v[1] for v in val_losses)
        print(f"  Meilleure Val Loss: {best_loss:.4f}")
        print(f"  Val Loss actuelle : {val_losses[-1][1]:.4f}")
    
    print(f"  Steps complétés   : {steps[-1] if steps else 0}")
    print(f"  Évaluations       : {len(history)}")


def main():
    print("=" * 60)
    print("VISUALISATION DES COURBES DE TRAINING")
    print("=" * 60)
    
    if not CSV_PATH.exists():
        print(f"\n❌ Pas de fichier de log : {CSV_PATH}")
        print(f"   Le training n'a peut-être pas encore commencé.")
        return
    
    history = load_history(CSV_PATH)
    
    if not history:
        print(f"\n⚠️ Le fichier est vide. Attends quelques évaluations.")
        return
    
    print(f"\n📂 {len(history)} points de mesure chargés")
    print(f"\n🎨 Génération des courbes...\n")
    
    plot_all_curves(history, MODEL_DIR)
    
    print(f"\n✅ Terminé !")
    print(f"\n📁 Fichiers dans : {MODEL_DIR}")


if __name__ == "__main__":
    main()