"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getApplicationStats } from "@/lib/api";

export function SummaryCards() {
  const { data: stats } = useQuery({
    queryKey: ["application-stats"],
    queryFn: getApplicationStats,
    refetchInterval: 10_000,
  });

  const cards = [
    { label: "Total", value: stats?.total ?? 0, color: "text-gray-900 dark:text-gray-100" },
    { label: "Running", value: stats?.running ?? 0, color: "text-blue-600" },
    { label: "Completed", value: stats?.completed ?? 0, color: "text-green-600" },
    { label: "Failed", value: stats?.failed ?? 0, color: "text-red-600" },
    { label: "Pending", value: stats?.pending ?? 0, color: "text-yellow-600" },
    { label: "Duplicate", value: stats?.duplicate ?? 0, color: "text-gray-500" },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
      {cards.map((card) => (
        <Card key={card.label}>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-500">
              {card.label}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold ${card.color}`}>
              {card.value}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
