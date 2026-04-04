import { useState, useEffect, useRef } from "react";
import { Plus, Trash2, Check, Loader2 } from "lucide-react";
import {
  getProductRules,
  createProductRule,
  deleteProductRule,
  updateProductRule,
} from "../api/client";
import toast from "react-hot-toast";

const CONDITION_TYPES = [
  { value: "stock_below", label: "Остаток ниже" },
  { value: "stock_above", label: "Остаток выше" },
  { value: "no_competitors", label: "Нет конкурентов" },
  { value: "margin_below", label: "Маржа ниже %" },
  { value: "margin_above", label: "Маржа выше %" },
];

const ACTION_TYPES = [
  { value: "raise_pct", label: "Поднять на %" },
  { value: "lower_pct", label: "Снизить на %" },
  { value: "set_min", label: "Установить мин." },
  { value: "set_max", label: "Установить макс." },
  { value: "set_price", label: "Установить цену" },
  { value: "add_rub", label: "Добавить ₽" },
  { value: "subtract_rub", label: "Убавить ₽" },
];

export default function RuleBuilder({ productId }) {
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState({});
  const saveTimers = useRef({});

  useEffect(() => {
    if (!productId) return;
    setLoading(true);
    getProductRules(productId)
      .then((res) => setRules(res.data))
      .catch(() => toast.error("Не удалось загрузить правила"))
      .finally(() => setLoading(false));
  }, [productId]);

  // Сохранение с debounce для числовых полей
  const scheduleSave = (ruleId, updatedRule) => {
    if (saveTimers.current[ruleId]) clearTimeout(saveTimers.current[ruleId]);
    setSaving((prev) => ({ ...prev, [ruleId]: true }));
    saveTimers.current[ruleId] = setTimeout(async () => {
      try {
        await updateProductRule(productId, ruleId, updatedRule);
      } catch {
        toast.error("Не удалось сохранить правило");
      } finally {
        setSaving((prev) => ({ ...prev, [ruleId]: false }));
      }
    }, 700);
  };

  // Мгновенное сохранение для селектов и тоглов
  const saveDirect = async (ruleId, updatedRule) => {
    setSaving((prev) => ({ ...prev, [ruleId]: true }));
    try {
      const res = await updateProductRule(productId, ruleId, updatedRule);
      setRules((prev) => prev.map((r) => (r.id === ruleId ? res.data : r)));
    } catch {
      toast.error("Не удалось сохранить правило");
    } finally {
      setSaving((prev) => ({ ...prev, [ruleId]: false }));
    }
  };

  const handleFieldChange = (ruleId, field, value) => {
    const updated = rules.map((r) => (r.id === ruleId ? { ...r, [field]: value } : r));
    setRules(updated);
    const rule = updated.find((r) => r.id === ruleId);
    // Числовые поля — с задержкой; всё остальное — сразу
    if (field === "condition_value" || field === "action_value") {
      scheduleSave(ruleId, rule);
    } else {
      saveDirect(ruleId, rule);
    }
  };

  const handleAdd = async () => {
    const newRule = {
      condition_type: "stock_below",
      condition_value: 0,
      action_type: "lower_pct",
      action_value: 5,
      is_active: true,
    };
    try {
      const res = await createProductRule(productId, newRule);
      setRules((prev) => [...prev, res.data]);
      toast.success("Правило добавлено");
    } catch {
      toast.error("Не удалось создать правило");
    }
  };

  const handleDelete = async (ruleId) => {
    try {
      await deleteProductRule(productId, ruleId);
      setRules((prev) => prev.filter((r) => r.id !== ruleId));
      toast.success("Правило удалено");
    } catch {
      toast.error("Не удалось удалить правило");
    }
  };

  if (loading) {
    return (
      <div className="text-sm" style={{ color: "var(--text-2)" }}>
        Загрузка правил…
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {rules.map((rule) => (
        <div
          key={rule.id}
          className="flex items-center gap-2 rounded-lg border p-2 text-sm"
          style={{
            borderColor: rule.is_active ? "rgba(124,58,237,0.3)" : "var(--border)",
            background: rule.is_active ? "rgba(124,58,237,0.03)" : "transparent",
          }}
        >
          {/* Condition type */}
          <select
            className="rounded-lg border px-2 py-1 text-sm"
            style={{
              color: "var(--text-2)",
              borderColor: "var(--border)",
              background: "hsl(var(--muted) / 0.3)",
            }}
            value={rule.condition_type}
            onChange={(e) => handleFieldChange(rule.id, "condition_type", e.target.value)}
          >
            {CONDITION_TYPES.map((ct) => (
              <option key={ct.value} value={ct.value}>
                {ct.label}
              </option>
            ))}
          </select>

          {/* Condition value */}
          {rule.condition_type !== "no_competitors" && (
            <input
              type="number"
              className="w-16 rounded-lg border px-2 py-1 text-sm"
              style={{
                color: "var(--text-2)",
                borderColor: "var(--border)",
                background: "hsl(var(--muted) / 0.3)",
              }}
              value={rule.condition_value}
              onChange={(e) =>
                handleFieldChange(rule.id, "condition_value", Number(e.target.value))
              }
              min="0"
            />
          )}

          {/* Action type */}
          <select
            className="rounded-lg border px-2 py-1 text-sm"
            style={{
              color: "var(--text-2)",
              borderColor: "var(--border)",
              background: "hsl(var(--muted) / 0.3)",
            }}
            value={rule.action_type}
            onChange={(e) => handleFieldChange(rule.id, "action_type", e.target.value)}
          >
            {ACTION_TYPES.map((at) => (
              <option key={at.value} value={at.value}>
                {at.label}
              </option>
            ))}
          </select>

          {/* Action value */}
          <input
            type="number"
            className="w-16 rounded-lg border px-2 py-1 text-sm"
            style={{
              color: "var(--text-2)",
              borderColor: "var(--border)",
              background: "hsl(var(--muted) / 0.3)",
            }}
            value={rule.action_value}
            onChange={(e) =>
              handleFieldChange(rule.id, "action_value", Number(e.target.value))
            }
            min="0"
          />

          {/* Active toggle */}
          <button
            className="rounded-lg px-2 py-1 text-sm font-medium transition-all"
            style={{
              backgroundColor: rule.is_active ? "#7c3aed" : "transparent",
              color: rule.is_active ? "#fff" : "var(--text-2)",
              border: rule.is_active ? "none" : "1px solid var(--border)",
            }}
            onClick={() => handleFieldChange(rule.id, "is_active", !rule.is_active)}
            title={rule.is_active ? "Выключить правило" : "Включить правило"}
          >
            {rule.is_active ? "Вкл" : "Выкл"}
          </button>

          {/* Save indicator */}
          <span className="w-4 flex items-center justify-center flex-shrink-0">
            {saving[rule.id] ? (
              <Loader2 size={12} className="animate-spin" style={{ color: "#7c3aed" }} />
            ) : (
              <Check size={12} style={{ color: "#16a34a", opacity: 0.6 }} />
            )}
          </span>

          {/* Delete */}
          <button
            onClick={() => handleDelete(rule.id)}
            className="ml-auto rounded-lg p-1 transition-colors hover:bg-red-500/10"
            style={{ color: "var(--text-2)" }}
            title="Удалить правило"
          >
            <Trash2 size={16} />
          </button>
        </div>
      ))}

      <button
        onClick={handleAdd}
        className="flex w-full items-center justify-center gap-1 rounded-lg border border-dashed px-3 py-2 text-sm font-medium transition-colors"
        style={{
          color: "#7c3aed",
          borderColor: "#7c3aed",
        }}
      >
        <Plus size={16} />
        Добавить правило
      </button>
    </div>
  );
}
