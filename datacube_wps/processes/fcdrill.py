from timeit import default_timer
import multiprocessing

import altair
import xarray
import numpy as np

from datacube.storage.masking import make_mask

from pywps.app.exceptions import ProcessError

from . import PolygonDrill, log_call, chart_dimensions


class FCDrill(PolygonDrill):
    SHORT_NAMES = ['BS', 'PV', 'NPV', 'Unobservable']
    LONG_NAMES = ['Bare Soil',
                  'Photosynthetic Vegetation',
                  'Non-Photosynthetic Vegetation',
                  'Unobservable']

    @log_call
    def process_data(self, data):
        # TODO raise ProcessError('query returned no data') when appropriate
        wofs_mask_flags = [
            dict(dry=True),
            dict(terrain_or_low_angle=False, high_slope=False, cloud_shadow=False, cloud=False, sea=False)
        ]

        water = data.data_vars['water']
        data = data.drop(['water'])

        total = data.count(dim=['x', 'y'])
        total_valid = (data != -1).sum(dim=['x', 'y'])

        for m in wofs_mask_flags:
            mask = make_mask(water, **m)
            data = data.where(mask)

        total_invalid = (np.isnan(data)).sum(dim=['x', 'y'])
        not_pixels = total_valid - (total - total_invalid)

        # following robbi's advice, cast the dataset to a dataarray
        maxFC = data.to_array(dim='variable', name='maxFC')

        # turn FC array into integer only as nanargmax doesn't seem to handle floats the way we want it to
        FC_int = maxFC.astype('int16')
        print('FC_int', FC_int)

        # use numpy.nanargmax to get the index of the maximum value along the variable dimension
        # BSPVNPV=np.nanargmax(FC_int, axis=0)
        BSPVNPV = FC_int.argmax(dim='variable')

        FC_mask = xarray.ufuncs.isfinite(maxFC).all(dim='variable')

        # #re-mask with nans to remove no-data
        BSPVNPV = BSPVNPV.where(FC_mask)

        FC_dominant = xarray.Dataset({
            'BS': (BSPVNPV == 0).where(FC_mask),
            'PV': (BSPVNPV == 1).where(FC_mask),
            'NPV': (BSPVNPV == 2).where(FC_mask)
        })

        FC_count = FC_dominant.sum(dim=['x', 'y'])

        # Fractional cover pixel count method
        # Get number of FC pixels, divide by total number of pixels per polygon
        new_ds = xarray.Dataset({
            'BS': (FC_count.BS / total_valid)['BS'] * 100,
            'PV': (FC_count.PV / total_valid)['PV'] * 100,
            'NPV': (FC_count.NPV / total_valid)['NPV'] * 100,
            'Unobservable': (not_pixels / total_valid)['BS'] * 100
        })

        print('calling dask with', multiprocessing.cpu_count())
        dask_time = default_timer()
        new_ds = new_ds.compute(scheduler='processes')
        print(new_ds)
        print('dask took exactly', default_timer() - dask_time)

        df = new_ds.to_dataframe()
        df.reset_index(inplace=True)
        return df

    def render_chart(self, df):
        width, height = chart_dimensions(self.style)

        melted = df.melt('time', var_name='Cover Type', value_name='Area')
        melted = melted.dropna()

        print('melted')
        print(melted)

        style = self.style['table']['columns']

        chart = altair.Chart(melted,
                             width=width,
                             height=height,
                             title='Percentage of Area - Fractional Cover')
        chart = chart.mark_area()
        chart = chart.encode(x='time:T',
                             y=altair.Y('Area:Q', stack='normalize'),
                             color=altair.Color('Cover Type:N',
                                                scale=altair.Scale(domain=self.SHORT_NAMES,
                                                                   range=[style[name]['chartLineColor']
                                                                          for name in self.LONG_NAMES])),
                             tooltip=[altair.Tooltip(field='time', format='%d %B, %Y', title='Date', type='temporal'),
                                      'Area:Q',
                                      'Cover Type:N'])

        return chart

    def render_outputs(self, df, chart):
        return super().render_outputs(df, chart, is_enabled=True, name="FC",
                                      header=self.LONG_NAMES)