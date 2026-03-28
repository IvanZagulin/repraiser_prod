import { formatMsk } from '../utils/date';
import { useState } from 'react';
import { FileText, Download, Loader2, Calendar, Clock, CheckCircle2 } from 'lucide-react';
import { generateReport } from '../api/client';
import toast from 'react-hot-toast';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

const PERIOD_OPTIONS = [
  { value: 7,   label: '7 дней',   desc: 'Последняя неделя' },
  { value: 14,  label: '14 дней',  desc: 'Две недели' },
  { value: 30,  label: '30 дней',  desc: 'Последний месяц' },
  { value: 90,  label: '90 дней',  desc: 'Последний квартал' },
  { value: 180, label: '180 дней', desc: 'Полгода' },
  { value: 365, label: '365 дней', desc: 'Год' },
];

export default function ReportsPage() {
  const [days, setDays] = useState(30);
  const [generating, setGenerating] = useState(false);
  const [lastGenerated, setLastGenerated] = useState(null);

  const handleGenerate = async () => {
    setGenerating(true);
    try {
      const res = await generateReport(days);
      const blob = new Blob([res.data], { type: 'application/pdf' });
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;

      // Generate clean filename — ignore content-disposition to avoid .pdf_ issue
      const today = new Date().toISOString().slice(0, 10);
      const filename = `QUASAR-X_report_${days}d_${today}.pdf`;

      link.setAttribute('download', filename);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setLastGenerated(new Date().toISOString());
      toast.success('Отчёт сгенерирован');
    } catch {
      toast.error('Ошибка генерации отчёта');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-foreground">Отчёты</h1>
        <p className="text-muted-foreground mt-2">
          Генерация PDF-отчётов для клиентов и аналитики
        </p>
      </div>

      <div className="max-w-4xl space-y-6">

        {/* Report type card */}
        <Card className="border" style={{ borderColor: 'rgba(0,200,255,.15)' }}>
          <CardContent className="p-6">
            <div className="flex items-center gap-3 mb-6">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{ background: 'rgba(0,200,255,.1)' }}>
                <FileText className="w-5 h-5" style={{ color: '#00c8ff' }} />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">Сводный отчёт</h2>
                <p className="text-sm text-muted-foreground">
                  Полный отчёт с аналитикой, запусками и изменениями цен
                </p>
              </div>
            </div>

            {/* What's included */}
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-6">
              {[
                'Сводка по запускам',
                'Изменения цен',
                'Топ товаров по изменениям',
                'Стратегии товаров',
                'Список аккаунтов',
                'История запусков',
              ].map((item) => (
                <div key={item} className="flex items-center gap-2 text-sm text-muted-foreground">
                  <CheckCircle2 className="w-3.5 h-3.5 text-[#00c8ff] shrink-0" />
                  {item}
                </div>
              ))}
            </div>

            <div className="border-t border-border pt-5">
              <p className="text-sm font-medium text-foreground mb-3 flex items-center gap-2">
                <Calendar className="w-4 h-4 text-muted-foreground" />
                Выберите период отчёта
              </p>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-5">
                {PERIOD_OPTIONS.map((opt) => {
                  const selected = days === opt.value;
                  return (
                    <button
                      key={opt.value}
                      onClick={() => setDays(opt.value)}
                      className={`rounded-xl border-2 px-3 py-3 text-center transition-all ${
                        selected
                          ? 'border-[#00c8ff] bg-[#00c8ff]/10 shadow-[0_0_12px_rgba(0,200,255,0.2)]'
                          : 'border-border bg-card hover:border-[#00c8ff]/40'
                      }`}
                    >
                      <span className={`text-sm font-bold block ${
                        selected ? 'text-[#00c8ff]' : 'text-foreground'
                      }`}>
                        {opt.label}
                      </span>
                      <span className="text-[10px] text-muted-foreground">{opt.desc}</span>
                    </button>
                  );
                })}
              </div>

              {/* Generate button */}
              <div className="flex items-center gap-4">
                <Button
                  onClick={handleGenerate}
                  disabled={generating}
                  className="gap-2 px-6"
                  style={{ background: generating ? undefined : '#00c8ff' }}
                >
                  {generating ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Генерация...
                    </>
                  ) : (
                    <>
                      <Download className="w-4 h-4" />
                      Скачать PDF
                    </>
                  )}
                </Button>

                {lastGenerated && (
                  <span className="text-xs text-muted-foreground flex items-center gap-1.5">
                    <Clock className="w-3.5 h-3.5" />
                    Последний отчёт: {formatMsk(lastGenerated, 'HH:mm')}
                  </span>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Info card */}
        <div className="card">
          <div className="flex items-start gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
              style={{ background: 'rgba(245,158,11,.1)' }}>
              <FileText className="w-4 h-4" style={{ color: '#f59e0b' }} />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground mb-1">Как использовать отчёты</p>
              <ul className="text-sm text-muted-foreground space-y-1">
                <li>• Отчёт содержит брендированную шапку QUASAR-X с логотипом</li>
                <li>• Таблицы с данными по аккаунтам, товарам и запускам</li>
                <li>• Можно отправить клиенту как подтверждение работы репрайсера</li>
                <li>• Для аналитики за больший период выберите 90 или 365 дней</li>
              </ul>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}
