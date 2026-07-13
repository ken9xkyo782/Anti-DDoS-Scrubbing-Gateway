export interface ServiceListItem {
  id: string
  name: string
  cidr_or_ip: string
  enabled: boolean
}

interface ServiceListProps {
  services: ServiceListItem[]
  selectedId: string | null
  onSelect: (serviceId: string) => void
}

export function ServiceList({ services, selectedId, onSelect }: ServiceListProps) {
  return (
    <section aria-labelledby="service-list-heading">
      <h2 id="service-list-heading">Services</h2>
      <ul>
        {services.map((service) => (
          <li key={service.id}>
            <button
              type="button"
              aria-pressed={service.id === selectedId}
              onClick={() => onSelect(service.id)}
            >
              {service.name} ({service.cidr_or_ip}){service.enabled ? '' : ' — disabled'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
