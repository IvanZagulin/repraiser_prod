import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/client';
import { Clock, Calendar, Pause, Play, Save, Loader2 } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

const INTERVAL_OPTIONS = [
  { value: 15,  label: '15 мин' },
  { value: 30,  label: '30 мин' },
  { value: 60,  label: '1 час' },
  { value: 120, label: '2 часа' },
];

const WEEKDAYS = [
  { num: 0, label: 'Пн' },
  { num: 1, label: 'Вт' },
  { num: 2, label: 'Ср' },
  { num: 3, label: 'Чт' },
  { num: 4, label: 'Пт' },
  { num: 5, label: 'Сб' },
  { num: 6, label: 'Вс' },
];

const TIMEZONES = [
  { value: 'Europe/Moscow',       label: 'Москва (UTC+3)' },
  { value: 'Europe/Samara',       label: 'Самара (UTC+4)' },
  { value: 'Asia/Yekaterinburg',  label: 'Екатеринбург (UTC+5)' },
  { value: 'Asia/Omsk',           label: 'Омск (UTC+6)' },
  { value: 'Asia/Krasnoyarsk',    label: 'Красноярск (UTC+7)' },
  { value: 'Asia/Irkutsk',        label: 'Иркутск (UTC+8)' },
  { value: 'Asia/Vladivostok',    label: 'Владивосток (UTC+10)' },
];

export default function SchedulePage() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [intervalMins, setIntervalMins] = useState(60);
  const [workStartHour, setWorkStartHour] = useState(0);
  const [workEndHour, setWorkEndHour] = useState(24);
  const [pausedDays, setPausedDays] = useState(new Set());
  const [timezone, setTimezone] = useState('Europe/Moscow');
  const [isActive, setIsActive] = useState(true);

  useEffect(() => {
    loadSchedule();
  }, []);

  const loadSchedule = async () => {
    try {
      const res = await api.get('/api/schedule');
      const d = res.data;
      setIntervalMins(d.interval_minutes ?? 60);
      setWorkStartHour(d.work_start_hour ?? 0);
      setWorkEndHour(d.work_end_hour ?? 24);
      setTimezone(d.timezone || 'Europe/Moscow');
      setIsActive(d.is_active ?? true);

      if (d.paused_days) {
        const nums = d.paused_days
          .split(',')
          .map((s) => parseInt(s.trim(), 10))
          .filter((n) => !isNaN(n));
        setPausedDays(new Set(nums));
      } else {
        setPausedDays(new Set());
      }
    } catch (err) {
      // Don't show error if it's just 404 (no schedule yet) — use defaults
      if (err.response?.status !== 404) {
        toast.error('Ошибка загрузки расписания');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.put('/api/schedule', {
        interval_minutes: intervalMins,
        work_start_hour: workStartHour,
        work_end_hour: workEndHour,
        paused_days: [...pausedDays].sort().join(','),
        timezone,
        is_active: isActive,
      });
      toast.success('Расписание сохранено');
    } catch {
      toast.error('Ошибка сохранения расписания');
    } finally {
      setSaving(false);
    }
  };

  const toggleDay = (num) => {
    setPausedDays((prev) => {
      const next = new Set(prev);
      if (next.has(num)) {
        next.delete(num);
      } else {
        next.add(num);
      }
      return next;
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-[#00c8ff]" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-foreground">Расписание</h1>
        <p className="text-muted-foreground mt-2">Настройка графика работы репрайсера</p>
      </div>

      <div className="max-w-4xl space-y-6">

        {/* ── Active Toggle ─────────────────────────────────────── */}
        <div className="card">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="bg-primary/10 p-2 rounded-lg">
                {isActive ? (
                  <Play className="w-5 h-5 text-[#00c8ff]" />
                ) : (
                  <Pause className="w-5 h-5 text-muted-foreground" />
                )}
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">
                  {isActive ? 'Репрайсер активен' : 'Репрайсер на паузе'}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {isActive
                    ? 'Цены обновляются по расписанию'
                    : 'Автоматическое обновление цен приостановлено'}
                </p>
              </div>
            </div>
            <button
              onClick={() => setIsActive((v) => !v)}
              className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors duration-200 focus:outline-none ${
                isActive ? 'bg-[#00c8ff]' : 'bg-muted border border-border'
              }`}
            >
              <span
                className={`inline-block h-6 w-6 transform rounded-full bg-white shadow-md transition-transform duration-200 ${
                  isActive ? 'translate-x-7' : 'translate-x-1'
                }`}
              />
            </button>
          </div>
        </div>

        {/* ── Interval Selector ─────────────────────────────────── */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="bg-primary/10 p-2 rounded-lg">
              <Clock className="w-5 h-5 text-[#00c8ff]" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Интервал обновления</h2>
              <p className="text-sm text-muted-foreground">Как часто репрайсер проверяет и меняет цены</p>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {INTERVAL_OPTIONS.map((opt) => {
              const selected = intervalMins === opt.value;
              return (
                <button
                  key={opt.value}
                  onClick={() => setIntervalMins(opt.value)}
                  className={`relative rounded-xl border-2 px-4 py-4 text-center transition-all duration-150 cursor-pointer ${
                    selected
                      ? 'border-[#00c8ff] bg-[#00c8ff]/10 shadow-[0_0_12px_rgba(0,200,255,0.25)]'
                      : 'border-border bg-card hover:border-[#00c8ff]/40'
                  }`}
                >
                  <span
                    className={`text-lg font-bold ${
                      selected ? 'text-[#00c8ff]' : 'text-foreground'
                    }`}
                  >
                    {opt.label}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* ── Working Hours ─────────────────────────────────────── */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="bg-primary/10 p-2 rounded-lg">
              <Calendar className="w-5 h-5 text-[#00c8ff]" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Рабочие часы</h2>
              <p className="text-sm text-muted-foreground">Репрайсер работает только в указанный промежуток</p>
            </div>
          </div>

          <div className="flex items-center gap-4 mb-5">
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">С (час)</label>
              <select
                value={workStartHour}
                onChange={(e) => setWorkStartHour(Number(e.target.value))}
                className="input w-24"
              >
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i} value={i}>
                    {String(i).padStart(2, '0')}:00
                  </option>
                ))}
              </select>
            </div>
            <span className="text-muted-foreground mt-5">—</span>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1">До (час)</label>
              <select
                value={workEndHour}
                onChange={(e) => setWorkEndHour(Number(e.target.value))}
                className="input w-24"
              >
                {Array.from({ length: 24 }, (_, i) => (
                  <option key={i + 1} value={i + 1}>
                    {String(i + 1).padStart(2, '0')}:00
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Visual Timeline */}
          <div className="relative">
            <div className="flex gap-px rounded-lg overflow-hidden">
              {Array.from({ length: 24 }, (_, h) => {
                const active = workStartHour < workEndHour
                  ? h >= workStartHour && h < workEndHour
                  : h >= workStartHour || h < workEndHour;
                return (
                  <div
                    key={h}
                    className={`h-6 flex-1 transition-colors duration-150 ${
                      active ? 'bg-[#00c8ff]/60' : 'bg-muted'
                    }`}
                    title={`${String(h).padStart(2, '0')}:00`}
                  />
                );
              })}
            </div>
            <div className="flex justify-between mt-1">
              <span className="text-[10px] text-muted-foreground">00</span>
              <span className="text-[10px] text-muted-foreground">06</span>
              <span className="text-[10px] text-muted-foreground">12</span>
              <span className="text-[10px] text-muted-foreground">18</span>
              <span className="text-[10px] text-muted-foreground">24</span>
            </div>
          </div>
        </div>

        {/* ── Paused Days ───────────────────────────────────────── */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="bg-primary/10 p-2 rounded-lg">
              <Pause className="w-5 h-5 text-[#00c8ff]" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Дни паузы</h2>
              <p className="text-sm text-muted-foreground">Выберите дни, когда репрайсер не работает</p>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            {WEEKDAYS.map((day) => {
              const paused = pausedDays.has(day.num);
              return (
                <button
                  key={day.num}
                  onClick={() => toggleDay(day.num)}
                  className={`w-14 h-14 rounded-xl font-semibold text-sm border-2 transition-all duration-150 ${
                    paused
                      ? 'border-border bg-muted text-muted-foreground line-through opacity-60'
                      : 'border-[#00c8ff] bg-[#00c8ff]/10 text-[#00c8ff] shadow-[0_0_8px_rgba(0,200,255,0.2)]'
                  }`}
                >
                  {day.label}
                </button>
              );
            })}
          </div>
          <p className="text-xs text-muted-foreground mt-3">
            Нажмите на день, чтобы поставить на паузу. Перечёркнутые дни — нерабочие.
          </p>
        </div>

        {/* ── Timezone ──────────────────────────────────────────── */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-4">
            <div className="bg-primary/10 p-2 rounded-lg">
              <Clock className="w-5 h-5 text-[#00c8ff]" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Часовой пояс</h2>
              <p className="text-sm text-muted-foreground">Рабочие часы привязаны к этому часовому поясу</p>
            </div>
          </div>
          <select
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            className="input w-full sm:w-80"
          >
            {TIMEZONES.map((tz) => (
              <option key={tz.value} value={tz.value}>
                {tz.label}
              </option>
            ))}
          </select>
        </div>

        {/* ── Save Button ───────────────────────────────────────── */}
        <button
          onClick={handleSave}
          disabled={saving}
          className="btn btn-primary flex items-center space-x-2 justify-center"
          style={{ minWidth: 220 }}
        >
          {saving ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Save className="w-4 h-4" />
          )}
          <span>{saving ? 'Сохранение...' : 'Сохранить расписание'}</span>
        </button>
      </div>
    </div>
  );
}
