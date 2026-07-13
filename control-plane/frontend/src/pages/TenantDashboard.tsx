import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { apiClient } from '../api/client'
import { ServiceList, type ServiceListItem } from '../components/ServiceList'
import { ServiceTelemetryPanel } from '../components/ServiceTelemetryPanel'

export function TenantDashboard() {
  const servicesQuery = useQuery({
    queryKey: ['services'],
    queryFn: () => apiClient<ServiceListItem[]>('/services'),
  })
  const [requestedId, setRequestedId] = useState<string | null>(null)

  if (servicesQuery.isPending) {
    return <p>Loading services…</p>
  }

  if (servicesQuery.isError) {
    return <p role="alert">Unable to load your services.</p>
  }

  const services = servicesQuery.data ?? []
  if (services.length === 0) {
    return <p>No services are available for this tenant.</p>
  }

  const selectedService = services.find((service) => service.id === requestedId) ?? services[0]

  return (
    <div>
      <h1>Tenant dashboard</h1>
      <ServiceList services={services} selectedId={selectedService.id} onSelect={setRequestedId} />
      <ServiceTelemetryPanel serviceId={selectedService.id} />
    </div>
  )
}
