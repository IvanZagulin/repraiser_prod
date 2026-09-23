import { useEffect, useState } from 'react';
import { PlayCircle, Download, RefreshCw, ChevronRight, ChevronDown, CheckCircle2, Loader2, Circle, ListFilter } from 'lucide-react';
import toast from 'react-hot-toast';
import { getRepricerRuns, exportRun } from '../api/client';
import { formatMsk } from '../utils/date';
import RunDetailModal from '../components/RunDetailModal';
import { StatusBadge } from '../components/StatusBadge';

export default function Runs() {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRun, setSelectedRun] = useState(null);
  const [expandedRunId, setExpandedRunId] = useState(null);

  useEffect(() => {
    loadRuns();
  }, []);

  // Авто-обновление каждые 3 секунды, пока есть активный запуск
  useEffect(() => {
    const hasRunning = runs.some((r) => r.status === 'running');
    if (!hasRunning) return;

    const timer = setInterval(async () => {
      try {
        const response = await getRepricerRuns({ limit: 50 });
        setRuns(response.data.runs);
      } catch {
        // тихий опрос без спама ошибками
      }
    }, 3000);

    return () => clearInterval(timer);
  }, [runs]);

  const loadRuns = async () => {
    setLoading(true);
    try {
      const response = await getRepricerRuns({ limit: 50 });
      setRuns(response.data.runs);
    } catch {
      toast.error('Ошибка загрузки запусков');
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async (runId) => {
    try {
      const response = await exportRun(runId);
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `run_${runId}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      toast.success('Файл загружен');
    } catch {
      toast.error('Ошибка экспорта');
    }
  };

  const getTypeLabel = (type) => {
    if (type === 'scheduled') return 'Авто';
    if (type === 'manual') return 'Ручной';
    return type;
  };

  const toggleExpand = (runId, e) => {
    e.stopPropagation();
    setExpandedRunId((prev) => (prev === runId ? null : runId));
  };

  return (
    <>
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="page-h1">Запуски репрайсера</h1>
          <p className="text-sm mt-1 text-muted-foreground">История запусков, этапы и результаты</p>
        </div>
        <button onClick={loadRuns} className="btn btn-secondary gap-2">
          <RefreshCw className="w-4 h-4" />
          Обновить
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="spinner" />
        </div>
      ) : runs.length === 0 ? (
        <div className="card text-center py-16">
          <PlayCircle className="w-10 h-10 mx-auto mb-3" style={{ color: '#e5e7eb' }} />
          <p style={{ color: 'var(--text-3)' }}>Запуски не найдены</p>
        </div>
      ) : (
        <div className="card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="dark-table">
              <thead>
                <tr>
                  <th style={{ width: '40px' }}></th>
                  <th>ID</th>
                  <th>Тип</th>
                  <th>Статус / Этап</th>
                  <th>Товаров</th>
                  <th>Обновлено</th>
                  <th>Пропущено</th>
                  <th>Ошибок</th>
                  <th>Запущен</th>
                  <th style={{ textAlign: 'right' }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => {
                  const isExpanded = expandedRunId === run.id;
                  const stages = run.stage_progress?.stages || [];
                  const processedCount = (run.updated_count || 0) + (run.skipped_count || 0) + (run.error_count || 0);
                  const progressPct = run.total_products > 0
                    ? Math.min(100, Math.round((processedCount / run.total_products) * 100))
                    : 0;

                  return (
                    <>
                      <tr
                        key={run.id}
                        onClick={() => toggleExpand(run.id, { stopPropagation: () => {} })}
                        style={{ cursor: 'pointer' }}
                        className={isExpanded ? 'bg-muted/30' : ''}
                      >
                        <td style={{ width: '40px', paddingRight: 0 }}>
                          <button
                            onClick={(e) => toggleExpand(run.id, e)}
                            className="p-1 hover:bg-muted rounded"
                          >
                            {isExpanded ? (
                              <ChevronDown className="w-4 h-4 text-primary" />
                            ) : (
                              <ChevronRight className="w-4 h-4 text-muted-foreground" />
                            )}
                          </button>
                        </td>
                        <td>
                          <span className="font-semibold text-foreground">#{run.id}</span>
                        </td>
                        <td>{getTypeLabel(run.run_type)}</td>
                        <td>
                          <div className="flex flex-col gap-0.5">
                            <StatusBadge status={run.status} />
                            {run.status === 'running' && run.current_stage && (
                              <span className="text-[11px] text-blue-500 font-medium animate-pulse">
                                {run.current_stage}
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="font-medium text-foreground">{run.total_products}</td>
                        <td className="font-semibold text-green-600">{run.updated_count}</td>
                        <td className="text-muted-foreground">{run.skipped_count}</td>
                        <td className="font-medium text-red-500">{run.error_count}</td>
                        <td>{formatMsk(run.started_at, 'dd.MM.yyyy HH:mm')}</td>
                        <td>
                          <div className="flex justify-end items-center gap-1.5">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleExport(run.id); }}
                              className="btn btn-xs btn-secondary"
                              title="Скачать отчет"
                            >
                              <Download className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); setSelectedRun(run); }}
                              className="btn btn-xs btn-outline"
                              title="Посмотреть изменения"
                            >
                              Детали
                            </button>
                          </div>
                        </td>
                      </tr>

                      {/* Раскрывающийся блок этапов прогона */}
                      {isExpanded && (
                        <tr className="bg-muted/20 border-b border-border">
                          <td colSpan={10} className="p-4">
                            <div className="space-y-3">
                              {/* Прогресс-бар для активных или завершенных прогонов */}
                              {run.total_products > 0 && (
                                <div>
                                  <div className="flex justify-between text-xs font-medium mb-1">
                                    <span className="text-foreground">
                                      {run.status === 'running' ? 'Прогресс обработки товаров:' : 'Итог обработки товаров:'}
                                    </span>
                                    <span className="text-primary font-semibold">
                                      {processedCount} из {run.total_products} ({progressPct}%)
                                    </span>
                                  </div>
                                  <div className="w-full bg-muted rounded-full h-2 overflow-hidden">
                                    <div
                                      className={`h-full transition-all duration-500 rounded-full ${
                                        run.status === 'failed' ? 'bg-red-500' : 'bg-primary'
                                      }`}
                                      style={{ width: `${progressPct}%` }}
                                    />
                                  </div>
                                </div>
                              )}

                              {/* Чек-лист этапов */}
                              {stages.length > 0 ? (
                                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-5 gap-2.5 pt-1">
                                  {stages.map((s) => (
                                    <div
                                      key={s.id}
                                      className={`p-2.5 rounded-lg border text-xs transition-all ${
                                        s.status === 'completed'
                                          ? 'border-green-500/30 bg-green-500/5'
                                          : s.status === 'running'
                                          ? 'border-blue-500/50 bg-blue-500/10 shadow-sm'
                                          : 'border-border/50 opacity-60 bg-muted/10'
                                      }`}
                                    >
                                      <div className="flex items-center gap-1.5 font-medium mb-1">
                                        {s.status === 'completed' && (
                                          <CheckCircle2 className="w-4 h-4 text-green-500 shrink-0" />
                                        )}
                                        {s.status === 'running' && (
                                          <Loader2 className="w-4 h-4 text-blue-500 animate-spin shrink-0" />
                                        )}
                                        {s.status === 'pending' && (
                                          <Circle className="w-4 h-4 text-muted-foreground/60 shrink-0" />
                                        )}
                                        <span className="text-foreground truncate">{s.name}</span>
                                      </div>
                                      {s.details && (
                                        <p className="text-[11px] text-muted-foreground mt-0.5 leading-tight">
                                          {s.details}
                                        </p>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              ) : (
                                <div className="text-xs text-muted-foreground">
                                  {run.status === 'running' ? (
                                    <div className="flex items-center gap-2 text-blue-500">
                                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                      <span>Выполняется: {run.current_stage || 'Инициализация...'}</span>
                                    </div>
                                  ) : (
                                    <span>Запуск завершен. Нажмите «Детали», чтобы посмотреть таблицу изменений цен.</span>
                                  )}
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>

    {selectedRun && (
      <RunDetailModal run={selectedRun} onClose={() => setSelectedRun(null)} />
    )}
    </>
  );
}
